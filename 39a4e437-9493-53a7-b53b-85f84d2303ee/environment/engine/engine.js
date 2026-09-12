#!/usr/bin/env node
'use strict';

// cicd is the trusted, immutable multiphase pipeline engine for the
// multiphase-cicd-carry gym. It threads an accumulating carry across a five
// phase pipeline and refuses to invent carry it did not thread. The engine
// lives outside the repair surface; the workspace driver that calls it is the
// only thing an operator edits.

const crypto = require('crypto');
const fs = require('fs');

const ORDER = ['lint', 'build', 'test', 'package', 'publish'];
const PRED = { build: 'lint', test: 'build', package: 'test', publish: 'package' };

function die(message) {
  process.stderr.write(message + '\n');
  process.exit(1);
}

function chain(phase, prev) {
  return crypto.createHash('sha256').update(phase + '|' + prev).digest('hex');
}

function load(path) {
  let data;
  try {
    data = fs.readFileSync(path, 'utf8');
  } catch (err) {
    if (err.code === 'ENOENT') {
      return [];
    }
    die('carry ' + path + ' is not readable: ' + err.message);
  }
  if (!data) {
    return [];
  }
  try {
    return JSON.parse(data);
  } catch (err) {
    die('carry ' + path + ' is not readable: ' + err.message);
  }
}

function save(path, recs) {
  const blob = JSON.stringify(recs, null, 2) + '\n';
  fs.writeFileSync(path + '.tmp', blob);
  fs.renameSync(path + '.tmp', path);
}

function journal(path, rec) {
  fs.appendFileSync(path + '.journal', rec.seq + '\t' + rec.phase + '\t' + rec.digest + '\n');
}

function inOrder(phase) {
  return ORDER.indexOf(phase) >= 0;
}

// verify walks a carry and confirms the digest chain, the predecessor linkage,
// the seq density and the carried counts. A carry that anyone hand edited or
// fabricated fails here, and that is exactly what the detector reads.
function verify(recs) {
  let prev = '';
  for (let i = 0; i < recs.length; i++) {
    const r = recs[i];
    if (r.seq !== i) {
      return 'record ' + i + ' carries seq ' + r.seq;
    }
    if (!inOrder(r.phase)) {
      return 'record ' + i + ' names unknown phase ' + r.phase;
    }
    if (r.prev !== prev) {
      return 'record ' + i + ' prev does not link';
    }
    if (r.digest !== chain(r.phase, prev)) {
      return 'record ' + i + ' digest does not recompute';
    }
    if (r.carried !== i) {
      return 'record ' + i + ' carried ' + r.carried + ' expected ' + i;
    }
    prev = r.digest;
  }
  return null;
}

function parse(args) {
  const opts = {};
  const rest = [];
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a.slice(0, 2) === '--') {
      opts[a.slice(2)] = i + 1 < args.length ? args[++i] : '';
    } else {
      rest.push(a);
    }
  }
  return { opts, rest };
}

function cmdPhase(args) {
  const { opts, rest } = parse(args);
  const phase = rest[0] || '';
  const carry = opts.carry || '';
  if (!phase || !carry) {
    die('usage: cicd phase <name> --carry <path>');
  }
  if (!inOrder(phase)) {
    die('phase ' + phase + ' is not part of the pipeline order');
  }
  const recs = load(carry);
  for (const r of recs) {
    if (r.phase === phase) {
      die('phase ' + phase + ' is already on this carry');
    }
  }
  const last = recs.length > 0 ? recs[recs.length - 1].phase : '';
  if (PRED[phase]) {
    if (last !== PRED[phase]) {
      die('phase ' + phase + ' must follow ' + PRED[phase] + ', but the carry last threaded ' + last);
    }
  } else if (recs.length !== 0) {
    die('phase ' + phase + ' must open a fresh pipeline, but the carry already threads ' + recs.length + ' records');
  }
  const prev = recs.length > 0 ? recs[recs.length - 1].digest : '';
  const rec = { seq: recs.length, phase: phase, prev: prev, digest: chain(phase, prev), carried: recs.length };
  recs.push(rec);
  save(carry, recs);
  journal(carry, rec);
  process.stdout.write('threaded ' + phase + ' seq=' + rec.seq + ' carried=' + rec.carried + '\n');
}

function cmdFold(args) {
  const { opts } = parse(args);
  const src = opts.carry || '';
  const dst = opts.into || '';
  if (!src || !dst) {
    die('usage: cicd fold --carry <path> --into <path> [--keep N]');
  }
  const recs = load(src);
  let keep = parseInt(opts.keep || '1', 10);
  if (isNaN(keep) || keep < 0) {
    keep = 0;
  }
  if (keep > recs.length) {
    keep = recs.length;
  }
  save(dst, recs.slice(recs.length - keep));
  process.stdout.write('fold wrote ' + keep + ' of ' + recs.length + ' records\n');
}

function cmdVerify(args) {
  const { opts } = parse(args);
  const carry = opts.carry || '';
  if (!carry) {
    die('usage: cicd verify --carry <path>');
  }
  const err = verify(load(carry));
  if (err) {
    die('carry does not verify: ' + err);
  }
  process.stdout.write('carry verifies\n');
}

function cmdReport(args) {
  const { opts } = parse(args);
  const carry = opts.carry || '';
  if (!carry) {
    die('usage: cicd report --carry <path>');
  }
  const recs = load(carry);
  const phases = recs.map(function (r) { return r.phase; });
  let complete = verify(recs) === null && recs.length === ORDER.length;
  if (complete) {
    for (let i = 0; i < ORDER.length; i++) {
      if (recs[i].phase !== ORDER[i] || recs[i].carried !== i) {
        complete = false;
        break;
      }
    }
  }
  const carriedFinal = recs.length > 0 ? recs[recs.length - 1].carried : 0;
  const out = { complete: complete, phases: phases, carried_final: carriedFinal, records: recs.length };
  process.stdout.write(JSON.stringify(out, null, 2) + '\n');
}

function main() {
  const args = process.argv.slice(2);
  const sub = args[0];
  const rest = args.slice(1);
  if (sub === 'phase') {
    cmdPhase(rest);
  } else if (sub === 'fold') {
    cmdFold(rest);
  } else if (sub === 'verify') {
    cmdVerify(rest);
  } else if (sub === 'report') {
    cmdReport(rest);
  } else {
    die('cicd <fold|phase|report|verify> ...');
  }
}

main();
