// relx is the marrowfleet-adjacent release orchestrator engine for the
// release-observe-repair gym. It threads an accumulating stage ledger across a
// five stage release and refuses to invent history it did not carry. The
// engine is trusted and immutable inside the sealed image; the repair surface
// is the workspace driver that calls it.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"sort"
)

// ORDER is the canonical release order. A stage may only be recorded when the
// last record already carried is its declared predecessor.
var ORDER = []string{"compile", "migrate", "canary", "promote", "finalize"}

var PRED = map[string]string{
	"migrate":  "compile",
	"canary":   "migrate",
	"promote":  "canary",
	"finalize": "promote",
}

// Record is one durable ledger entry. Carried is the number of prior records
// the stage threaded when it ran; on a healthy release it equals the record
// index, so a collapsed history shows up as a carried count below the index.
type Record struct {
	Seq      int    `json:"seq"`
	Stage    string `json:"stage"`
	Prev     string `json:"prev"`
	Artifact string `json:"artifact"`
	Carried  int    `json:"carried"`
}

func die(format string, args ...interface{}) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	os.Exit(1)
}

func loadLedger(path string) []Record {
	data, err := os.ReadFile(path)
	if err != nil || len(data) == 0 {
		return []Record{}
	}
	var recs []Record
	if err := json.Unmarshal(data, &recs); err != nil {
		die("ledger %s is not readable: %v", path, err)
	}
	return recs
}

func saveLedger(path string, recs []Record) {
	blob, err := json.MarshalIndent(recs, "", "  ")
	if err != nil {
		die("ledger could not be serialised: %v", err)
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, append(blob, '\n'), 0o644); err != nil {
		die("ledger could not be staged: %v", err)
	}
	if err := os.Rename(tmp, path); err != nil {
		die("ledger could not be committed: %v", err)
	}
}

func journal(path string, rec Record) {
	line := fmt.Sprintf("%d\t%s\t%s\n", rec.Seq, rec.Stage, rec.Artifact)
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		die("journal could not be opened: %v", err)
	}
	defer f.Close()
	if _, err := f.WriteString(line); err != nil {
		die("journal could not be appended: %v", err)
	}
}

func chain(stage, prev string) string {
	sum := sha256.Sum256([]byte(stage + "|" + prev))
	return hex.EncodeToString(sum[:])
}

func inOrder(stage string) bool {
	for _, s := range ORDER {
		if s == stage {
			return true
		}
	}
	return false
}

// verify walks a ledger and confirms the artifact chain, the predecessor
// linkage, the seq density and the carried counts. A ledger that anyone hand
// edited or fabricated fails here, and that is exactly what the detector reads.
func verify(recs []Record) error {
	prev := ""
	for i, r := range recs {
		if r.Seq != i {
			return fmt.Errorf("record %d carries seq %d", i, r.Seq)
		}
		if !inOrder(r.Stage) {
			return fmt.Errorf("record %d names unknown stage %q", i, r.Stage)
		}
		if r.Prev != prev {
			return fmt.Errorf("record %d prev %q does not link to %q", i, r.Prev, prev)
		}
		want := chain(r.Stage, prev)
		if r.Artifact != want {
			return fmt.Errorf("record %d artifact does not recompute", i)
		}
		if r.Carried != i {
			return fmt.Errorf("record %d carried %d prior records, expected %d", i, r.Carried, i)
		}
		prev = r.Artifact
	}
	return nil
}

func cmdStage(args []string) {
	fs := parse(args, map[string]*string{"ledger": ptr("")})
	stage := ""
	if len(fs.rest) > 0 {
		stage = fs.rest[0]
	}
	ledgerPath := *fs.opts["ledger"]
	if stage == "" || ledgerPath == "" {
		die("usage: relx stage <name> --ledger <path>")
	}
	if !inOrder(stage) {
		die("stage %q is not part of the release order", stage)
	}
	recs := loadLedger(ledgerPath)
	for _, r := range recs {
		if r.Stage == stage {
			die("stage %q is already on this ledger", stage)
		}
	}
	last := ""
	if len(recs) > 0 {
		last = recs[len(recs)-1].Stage
	}
	if pred, ok := PRED[stage]; ok {
		if last != pred {
			die("stage %q must follow %q, but the ledger last carried %q", stage, pred, last)
		}
	} else if len(recs) != 0 {
		die("stage %q must open a fresh release, but the ledger already carries %d records", stage, len(recs))
	}
	prev := ""
	if len(recs) > 0 {
		prev = recs[len(recs)-1].Artifact
	}
	rec := Record{
		Seq:      len(recs),
		Stage:    stage,
		Prev:     prev,
		Artifact: chain(stage, prev),
		Carried:  len(recs),
	}
	recs = append(recs, rec)
	saveLedger(ledgerPath, recs)
	journal(ledgerPath+".journal", rec)
	fmt.Printf("recorded %s seq=%d carried=%d\n", stage, rec.Seq, rec.Carried)
}

func cmdSnapshot(args []string) {
	fs := parse(args, map[string]*string{"ledger": ptr(""), "into": ptr(""), "last": ptr("1")})
	src := *fs.opts["ledger"]
	dst := *fs.opts["into"]
	if src == "" || dst == "" {
		die("usage: relx snapshot --ledger <path> --into <path> [--last N]")
	}
	recs := loadLedger(src)
	n := atoi(*fs.opts["last"])
	if n < 0 {
		n = 0
	}
	if n > len(recs) {
		n = len(recs)
	}
	saveLedger(dst, recs[len(recs)-n:])
	fmt.Printf("snapshot wrote %d of %d records\n", n, len(recs))
}

func cmdVerify(args []string) {
	fs := parse(args, map[string]*string{"ledger": ptr("")})
	path := *fs.opts["ledger"]
	if path == "" {
		die("usage: relx verify --ledger <path>")
	}
	if err := verify(loadLedger(path)); err != nil {
		die("ledger does not verify: %v", err)
	}
	fmt.Println("ledger verifies")
}

func cmdManifest(args []string) {
	fs := parse(args, map[string]*string{"ledger": ptr("")})
	path := *fs.opts["ledger"]
	if path == "" {
		die("usage: relx manifest --ledger <path>")
	}
	recs := loadLedger(path)
	stages := make([]string, 0, len(recs))
	for _, r := range recs {
		stages = append(stages, r.Stage)
	}
	complete := verify(recs) == nil && len(recs) == len(ORDER)
	if complete {
		for i, s := range ORDER {
			if recs[i].Stage != s || recs[i].Carried != i {
				complete = false
				break
			}
		}
	}
	carriedFinal := 0
	if len(recs) > 0 {
		carriedFinal = recs[len(recs)-1].Carried
	}
	out := map[string]interface{}{
		"complete":      complete,
		"stages":        stages,
		"carried_final": carriedFinal,
		"records":       len(recs),
	}
	blob, _ := json.MarshalIndent(out, "", "  ")
	fmt.Println(string(blob))
}

type flags struct {
	opts map[string]*string
	rest []string
}

func ptr(s string) *string { return &s }

func atoi(s string) int {
	n := 0
	neg := false
	for i, c := range s {
		if i == 0 && c == '-' {
			neg = true
			continue
		}
		if c < '0' || c > '9' {
			return 0
		}
		n = n*10 + int(c-'0')
	}
	if neg {
		return -n
	}
	return n
}

func parse(args []string, defaults map[string]*string) flags {
	f := flags{opts: map[string]*string{}, rest: []string{}}
	for k, v := range defaults {
		f.opts[k] = v
	}
	i := 0
	for i < len(args) {
		a := args[i]
		if len(a) > 2 && a[:2] == "--" {
			key := a[2:]
			val := ""
			if i+1 < len(args) {
				val = args[i+1]
				i += 2
			} else {
				i++
			}
			if _, ok := f.opts[key]; ok {
				f.opts[key] = ptr(val)
			}
			continue
		}
		f.rest = append(f.rest, a)
		i++
	}
	return f
}

func usage() {
	cmds := []string{"stage", "snapshot", "manifest", "verify"}
	sort.Strings(cmds)
	die("relx <%s> ...", join(cmds, "|"))
}

func join(xs []string, sep string) string {
	out := ""
	for i, x := range xs {
		if i > 0 {
			out += sep
		}
		out += x
	}
	return out
}

func main() {
	if len(os.Args) < 2 {
		usage()
	}
	switch os.Args[1] {
	case "stage":
		cmdStage(os.Args[2:])
	case "snapshot":
		cmdSnapshot(os.Args[2:])
	case "manifest":
		cmdManifest(os.Args[2:])
	case "verify":
		cmdVerify(os.Args[2:])
	default:
		usage()
	}
}
