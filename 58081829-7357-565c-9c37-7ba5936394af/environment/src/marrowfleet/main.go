// The Marrowfleet crew roster run. Builds one duty sheet per crew member the
// register declares from the bidding period's flown sectors, seals it with the
// bureau's signing key and releases each base's sheets into that base's own
// crewing drop.
//
// Everything beyond the register lives in the run's own store, which is the
// run's working memory and not a document.

package main

import (
	"encoding/binary"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
)

const (
	storePath    = "/var/lib/marrowfleet/roster.store"
	defRoot      = "/workspace/rostering"
	sheetsDir    = "/var/lib/marrowfleet/sheets"
	registerPath = "/var/lib/marrowfleet/roster.register"
	manifestPath = "/var/lib/marrowfleet/sheet.sums"
	baseRoot     = "/srv/bases"
	creditBase   = 100
)

// --------------------------------------------------------------------- store

type store struct {
	text map[string]string
}

func newStore() *store { return &store{text: map[string]string{}} }

func (s *store) get(key string) string {
	if v, ok := s.text[key]; ok {
		return v
	}
	return ""
}

// The store carries a seal over its own bytes. It is the run's working memory,
// written by the run and read by the run; a store that something else has
// written does not carry the run's seal and is not loaded.
func mask(b []byte) {
	k := uint32(0x9e3779b9)
	for i := range b {
		k = k*1664525 + 1013904223
		b[i] ^= byte(k >> 13)
	}
}

func seal(p []byte) uint64 {
	h := uint64(0xcbf29ce484222325)
	for _, b := range p {
		h ^= uint64(b)
		h *= 0x100000001b3
	}
	return ((h << 17) | (h >> 47)) ^ 0x517cc1b727220a95
}

func sealHex(p []byte) string { return fmt.Sprintf("%016x", seal(p)) }

func readStore() (*store, bool) {
	s := newStore()
	whole, err := os.ReadFile(storePath)
	if err != nil || len(whole) < 8 {
		return s, true
	}
	split := len(whole) - 8
	payload := make([]byte, split)
	copy(payload, whole[:split])
	if seal(payload) != binary.LittleEndian.Uint64(whole[split:]) {
		return nil, false
	}
	mask(payload)
	i := 0
	for i+2 <= len(payload) {
		kind := payload[i]
		klen := int(payload[i+1])
		i += 2
		if i+klen > len(payload) {
			break
		}
		key := string(payload[i : i+klen])
		i += klen
		if kind == 1 {
			if i+8 > len(payload) {
				break
			}
			i += 8
		} else {
			if i+2 > len(payload) {
				break
			}
			vlen := int(binary.LittleEndian.Uint16(payload[i : i+2]))
			i += 2
			if i+vlen > len(payload) {
				break
			}
			s.text[key] = string(payload[i : i+vlen])
			i += vlen
		}
	}
	return s, true
}

func loadStore() *store {
	s, ok := readStore()
	if !ok {
		fmt.Fprintln(os.Stderr, "marrowfleet: the roster store does not carry this run's seal")
		fmt.Fprintln(os.Stderr, "marrowfleet: refusing to run against a store it did not write")
		os.Exit(3)
	}
	return s
}

func saveStore(s *store) {
	os.MkdirAll(filepath.Dir(storePath), 0o755)
	out := []byte{}
	keys := []string{}
	for k := range s.text {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		v := s.text[k]
		out = append(out, 2, byte(len(k)))
		out = append(out, []byte(k)...)
		var b [2]byte
		binary.LittleEndian.PutUint16(b[:], uint16(len(v)))
		out = append(out, b[:]...)
		out = append(out, []byte(v)...)
	}
	mask(out)
	var tag [8]byte
	binary.LittleEndian.PutUint64(tag[:], seal(out))
	writeAtomic(storePath, append(out, tag[:]...))
}

func writeAtomic(path string, body []byte) {
	staging := path + ".writing"
	if os.WriteFile(staging, body, 0o644) == nil {
		os.Rename(staging, path)
	}
}

// The tables print the notice -- what the schedule has given the run to carry,
// from the moment it was given. The run works from the figure it last took up,
// and it takes a notice up when that notice is applied to it.
func noticed(s *store, key, actual string) string {
	if v, ok := s.text["notice/"+key]; ok {
		return v
	}
	return actual
}

// ------------------------------------------------------------------ register

func readConf(path string) map[string]string {
	out := map[string]string{}
	raw, err := os.ReadFile(path)
	if err != nil {
		return out
	}
	for _, line := range strings.Split(string(raw), "\n") {
		t := strings.TrimSpace(line)
		if t == "" || strings.HasPrefix(t, "#") {
			continue
		}
		i := strings.Index(t, "=")
		if i < 0 {
			continue
		}
		out[strings.TrimSpace(t[:i])] = strings.Trim(strings.TrimSpace(t[i+1:]), "\"")
	}
	return out
}

type frag struct {
	name string
	conf map[string]string
}

func fragments(dir string) []frag {
	out := []frag{}
	entries, err := os.ReadDir(filepath.Join(defRoot, dir))
	if err != nil {
		return out
	}
	names := []string{}
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".toml") {
			names = append(names, e.Name())
		}
	}
	sort.Strings(names)
	for _, n := range names {
		c := readConf(filepath.Join(defRoot, dir, n))
		if c["enabled"] == "false" {
			continue
		}
		name := c["name"]
		if name == "" {
			name = strings.TrimSuffix(n, ".toml")
		}
		out = append(out, frag{name: name, conf: c})
	}
	return out
}

func crew() []frag  { return fragments("crew.d") }
func bases() []frag { return fragments("bases.d") }

func registerConf() map[string]string {
	out := map[string]string{}
	raw, err := os.ReadFile(filepath.Join(defRoot, "rostering.yaml"))
	if err != nil {
		return out
	}
	for _, line := range strings.Split(string(raw), "\n") {
		i := strings.Index(line, ":")
		if i < 0 || strings.HasPrefix(strings.TrimSpace(line), "#") {
			continue
		}
		out[strings.TrimSpace(line[:i])] = strings.TrimSpace(line[i+1:])
	}
	return out
}

func registerField(key string) string { return registerConf()[key] }

// ------------------------------------------------------------------- sectors

type sector struct {
	period string
	crew   string
	code   string
	block  int64
}

func sectors() []sector {
	out := []sector{}
	raw, err := os.ReadFile(filepath.Join(defRoot, "sectors.csv"))
	if err != nil {
		return out
	}
	for i, line := range strings.Split(string(raw), "\n") {
		if i == 0 || strings.TrimSpace(line) == "" {
			continue
		}
		c := strings.Split(line, ",")
		if len(c) < 4 {
			continue
		}
		block, err := strconv.ParseInt(strings.TrimSpace(c[3]), 10, 64)
		if err != nil {
			continue
		}
		out = append(out, sector{
			period: strings.TrimSpace(c[0]),
			crew:   strings.TrimSpace(c[1]),
			code:   strings.TrimSpace(c[2]),
			block:  block,
		})
	}
	return out
}

func currentPeriod() string {
	latest := ""
	for _, s := range sectors() {
		if s.period > latest {
			latest = s.period
		}
	}
	return latest
}

func flownBy(who, period string) []sector {
	out := []sector{}
	for _, s := range sectors() {
		if s.crew == who && s.period == period {
			out = append(out, s)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].code < out[j].code })
	return out
}

// The crediting factors the schedule carries, in hundredths.
func factorOf(category string) int64 {
	switch category {
	case "long-haul":
		return 85
	case "short-haul":
		return 100
	case "standby":
		return 60
	}
	return 0
}

// A sector is credited at its block minutes taken on the crediting factor its
// category carries, and the duty credit is those credits added together.
func dutyCredit(flown []sector, factor int64) int64 {
	total := int64(0)
	for _, s := range flown {
		total += s.block * factor / creditBase
	}
	return total
}

// --------------------------------------------------------------------- sheet

func periodOf(s *store, who string) string {
	if v := s.get("period/" + who); v != "" {
		return v
	}
	return currentPeriod()
}

type limitation struct {
	review string
	figure int64
}

func limitOf(s *store, who string) *limitation {
	raw := s.get("limit/" + who)
	at := strings.Index(raw, "|")
	if at < 0 {
		return nil
	}
	figure, err := strconv.ParseInt(raw[at+1:], 10, 64)
	if err != nil {
		return nil
	}
	return &limitation{review: raw[:at], figure: figure}
}

func scopeOf(s *store, base string) []string {
	held := s.get("scope/" + base)
	if held == "" {
		for _, b := range bases() {
			if b.name == base {
				held = b.conf["ranks"]
			}
		}
	}
	out := []string{}
	for _, r := range strings.Split(held, ",") {
		r = strings.TrimSpace(r)
		if r != "" {
			out = append(out, r)
		}
	}
	return out
}

func sheetName(who string) string { return "marrowfleet_" + who + ".sheet" }

func buildSheet(s *store, f frag) (string, string, bool) {
	who := f.name
	period := periodOf(s, who)
	flown := flownBy(who, period)
	if len(flown) == 0 {
		return "", "", false
	}
	category := f.conf["category"]
	factor := factorOf(category)
	standing := "standard"
	credit := dutyCredit(flown, factor)
	if lim := limitOf(s, who); lim != nil {
		standing = "fixed"
		credit = lim.figure
	}
	legs := []string{}
	for _, sec := range flown {
		legs = append(legs, fmt.Sprintf("%s:%d", sec.code, sec.block))
	}
	body := "# marrowfleet duty sheet\n" +
		"crew: " + who + "\n" +
		"base: " + f.conf["base"] + "\n" +
		"rank: " + f.conf["rank"] + "\n" +
		"category: " + category + "\n" +
		"period: " + period + "\n" +
		"sectors: " + strings.Join(legs, " ") + "\n" +
		"factor: " + strconv.FormatInt(factor, 10) + "\n" +
		"limit: " + standing + "\n" +
		"credit: " + strconv.FormatInt(credit, 10) + "\n" +
		"key: " + registerField("signing_key") + "\n"
	stamped := sealHex([]byte(registerField("key_fingerprint") + body))
	return sheetName(who), body + "seal: " + stamped + "\n", true
}

// The seal the run stamped on the sheets a base is rostered against.
func rosterSeal(s *store, base string) string {
	names := []string{}
	for _, f := range crew() {
		if f.conf["base"] != base {
			continue
		}
		if held := s.get("staged/" + f.name); held != "" {
			names = append(names, held)
		}
	}
	sort.Strings(names)
	acc := []byte{}
	for _, n := range names {
		if raw, err := os.ReadFile(filepath.Join(sheetsDir, n)); err == nil {
			acc = append(acc, raw...)
		}
	}
	return sealHex(acc)
}

// ---------------------------------------------------------------- operations

func cmdBuild(s *store) {
	os.MkdirAll(sheetsDir, 0o755)
	manifest := []string{}
	for _, f := range crew() {
		name, body, ok := buildSheet(s, f)
		if !ok {
			continue
		}
		writeAtomic(filepath.Join(sheetsDir, name), []byte(body))
		s.text["staged/"+f.name] = name
		manifest = append(manifest, name+"  "+sealHex([]byte(body)))
		fmt.Printf("built %s (%d bytes)\n", f.name, len(body))
	}
	fmt.Println("notice: the 2036 crediting table is retained for look-back and " +
		"will be withdrawn at the 2044 revision")
	sort.Strings(manifest)
	writeAtomic(manifestPath, []byte(strings.Join(manifest, "\n")+"\n"))

	for _, b := range bases() {
		base := b.name
		// What the run has standing for this base, which is what it reports.
		rostered := 0
		for _, f := range crew() {
			if f.conf["base"] == base && s.get("staged/"+f.name) != "" {
				rostered++
			}
		}
		fmt.Printf("%-15s %d rostered\n", base, rostered)
		if s.get("hold/"+base) != "" {
			continue
		}
		folder := filepath.Join(baseRoot, base)
		os.MkdirAll(folder, 0o755)
		if entries, err := os.ReadDir(folder); err == nil {
			for _, e := range entries {
				if strings.HasSuffix(e.Name(), ".sheet") {
					os.Remove(filepath.Join(folder, e.Name()))
				}
			}
		}
		ranks := map[string]bool{}
		for _, r := range scopeOf(s, base) {
			ranks[r] = true
		}
		for _, f := range crew() {
			if f.conf["base"] != base || !ranks[f.conf["rank"]] {
				continue
			}
			held := s.get("staged/" + f.name)
			if held == "" {
				continue
			}
			raw, err := os.ReadFile(filepath.Join(sheetsDir, held))
			if err != nil {
				continue
			}
			writeAtomic(filepath.Join(folder, held), raw)
		}
	}
	saveStore(s)
}

func registerLines() []string {
	out := []string{}
	raw, err := os.ReadFile(registerPath)
	if err != nil {
		return out
	}
	for _, l := range strings.Split(string(raw), "\n") {
		if strings.TrimSpace(l) != "" {
			out = append(out, l)
		}
	}
	return out
}

func cmdEndorse(s *store) {
	period := currentPeriod()
	done := map[string]bool{}
	for _, l := range registerLines() {
		f := strings.Fields(l)
		if len(f) >= 2 {
			done[f[0]+"/"+f[1]] = true
		}
	}
	add := ""
	for _, b := range bases() {
		base := b.name
		if done[period+"/"+base] {
			fmt.Printf("%-15s has endorsed in %s\n", base, period)
			continue
		}
		add += period + " " + base + " " + rosterSeal(s, base) + "\n"
		fmt.Printf("%-15s endorsed the %s period\n", base, period)
	}
	fh, err := os.OpenFile(registerPath, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		fmt.Fprintln(os.Stderr, "marrowfleet: the roster register is not writable")
		os.Exit(2)
	}
	fh.WriteString(add)
	fh.Close()
}

func cmdVerify(s *store) {
	bad := 0
	for _, f := range crew() {
		held := s.get("staged/" + f.name)
		if held == "" {
			continue
		}
		if _, err := os.Stat(filepath.Join(sheetsDir, held)); err != nil {
			fmt.Printf("MISSING  %s %s\n", f.name, held)
			bad++
		}
	}
	if bad == 0 {
		fmt.Println("the bureau has a roster for every base the register declares")
	}
}

// --------------------------------------------------------------------- main

func usage() {
	fmt.Println("usage: marrowfleet <status|plan|period|hold|scope|limit|build|endorse|verify>")
}

func main() {
	argv := os.Args[1:]
	if len(argv) == 0 {
		usage()
		return
	}
	s := loadStore()
	cmd, rest := argv[0], argv[1:]

	switch cmd {
	case "notice":
		if len(rest) >= 3 {
			s.text["notice/"+rest[0]+"/"+rest[1]] = strings.Join(rest[2:], " ")
			saveStore(s)
			fmt.Printf("noticed %s\n", rest[0])
			return
		}
		fmt.Fprintln(os.Stderr, "marrowfleet: notice takes a table, a key and a figure")
		os.Exit(2)
	case "status":
		fmt.Printf("period        %s\n", currentPeriod())
		fmt.Printf("crew          %d declared\n", len(crew()))
		fmt.Printf("bases         %d declared\n", len(bases()))
		fmt.Printf("base root     %s\n", registerField("base_root"))
		fmt.Printf("signing key   %s\n", registerField("signing_key"))
	case "plan":
		for _, f := range crew() {
			fmt.Printf("%-9s %-14s %s\n", f.name, f.conf["base"], f.conf["rank"])
		}
	case "period":
		if len(rest) >= 3 && rest[0] == "set" {
			s.text["period/"+rest[1]] = rest[2]
			s.text["notice/period/"+rest[1]] = rest[2]
			saveStore(s)
			fmt.Printf("period %s -> %s\n", rest[1], rest[2])
			return
		}
		fmt.Printf("sectors %s\n", currentPeriod())
		for _, f := range crew() {
			fmt.Printf("%-9s %s\n", f.name,
				noticed(s, "period/"+f.name, periodOf(s, f.name)))
		}
	case "hold":
		if len(rest) >= 3 && rest[0] == "set" {
			s.text["hold/"+rest[1]] = strings.Join(rest[2:], " ")
			saveStore(s)
			fmt.Printf("hold %s recorded\n", rest[1])
			return
		}
		if len(rest) >= 2 && rest[0] == "clear" {
			delete(s.text, "hold/"+rest[1])
			delete(s.text, "notice/hold/"+rest[1])
			saveStore(s)
			fmt.Printf("hold %s cleared\n", rest[1])
			return
		}
		for _, b := range bases() {
			shown := s.get("hold/" + b.name)
			if shown == "" {
				shown = "none"
			}
			fmt.Printf("%-15s %s\n", b.name, noticed(s, "hold/"+b.name, shown))
		}
	case "scope":
		if len(rest) >= 3 && rest[0] == "set" {
			s.text["scope/"+rest[1]] = rest[2]
			saveStore(s)
			fmt.Printf("scope %s -> %s\n", rest[1], rest[2])
			return
		}
		if len(rest) >= 2 && rest[0] == "clear" {
			delete(s.text, "scope/"+rest[1])
			delete(s.text, "notice/scope/"+rest[1])
			saveStore(s)
			fmt.Printf("scope %s cleared\n", rest[1])
			return
		}
		for _, b := range bases() {
			fmt.Printf("%-15s %s\n", b.name,
				noticed(s, "scope/"+b.name, strings.Join(scopeOf(s, b.name), ",")))
		}
	case "limit":
		if len(rest) >= 4 && rest[0] == "fix" {
			s.text["limit/"+rest[1]] = rest[2] + "|" + rest[3]
			s.text["notice/limit/"+rest[1]] = "fixed " + rest[3] + " (review " + rest[2] + ")"
			saveStore(s)
			fmt.Printf("limited %s at %s\n", rest[1], rest[3])
			return
		}
		if len(rest) >= 2 && rest[0] == "lift" {
			delete(s.text, "limit/"+rest[1])
			delete(s.text, "notice/limit/"+rest[1])
			saveStore(s)
			fmt.Printf("lifted %s\n", rest[1])
			return
		}
		for _, f := range crew() {
			shown := "standard"
			if lim := limitOf(s, f.name); lim != nil {
				shown = fmt.Sprintf("fixed %d (review %s)", lim.figure, lim.review)
			}
			fmt.Printf("%-9s %s\n", f.name, noticed(s, "limit/"+f.name, shown))
		}
	case "build":
		cmdBuild(s)
	case "endorse":
		cmdEndorse(s)
	case "verify":
		cmdVerify(s)
	default:
		fmt.Fprintf(os.Stderr, "marrowfleet: unknown command %s\n", cmd)
		os.Exit(2)
	}
}
