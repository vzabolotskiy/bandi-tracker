import assert from "node:assert/strict";
import { test } from "node:test";
import { verdict } from "../assets/eligibility.js";

const call = (type, extra = {}) => ({ type, deadline: "2026-11-15T12:00", institution: "Univ. BOLOGNA", gsd: "13/ECON-04", title: "x", ...extra });
const base = { lmDate: "2019-07", phd: "obtained", phdDate: "2024-03", degreeCountry: "it", citizenship: "eu", permanent: "no", months: {} };

test("empty profile -> unknown", () => {
  assert.equal(verdict(call("rtt"), {}).status, "unknown");
});
test("fresh Italian PhD is eligible for pre-tenure and RTT", () => {
  for (const t of ["incarico_postdoc", "contratto_ricerca", "rtt"]) assert.equal(verdict(call(t), base).status, "yes", t);
});
test("incarico di ricerca: master's older than 6 years blocks", () => {
  const v = verdict(call("incarico_ricerca"), { ...base, lmDate: "2019-07" });
  assert.equal(v.status, "no");
  assert.match(v.reasons[0], /6 years/);
  assert.equal(verdict(call("incarico_ricerca"), { ...base, lmDate: "2022-07" }).status, "yes");
});
test("caps", () => {
  assert.equal(verdict(call("incarico_postdoc"), { ...base, months: { postdoc: 30 } }).status, "no");
  assert.equal(verdict(call("incarico_postdoc"), { ...base, months: { postdoc: 24 } }).status, "yes");
  assert.equal(verdict(call("contratto_ricerca"), { ...base, months: { contratti: 40 } }).status, "no");
  assert.equal(verdict(call("contratto_ricerca"), { ...base, months: { incarichi: 36, postdoc: 36, contratti: 48 } }).status, "no"); // 120+24>132
});
test("past RTD holders excluded from pre-tenure, not from RTT", () => {
  const p = { ...base, months: { rtd: 24 } };
  assert.equal(verdict(call("incarico_postdoc"), p).status, "no");
  assert.equal(verdict(call("rtt"), p).status, "yes");
  assert.equal(verdict(call("rtt"), { ...base, months: { rtt: 36 } }).status, "no");
});
test("permanent staff", () => {
  assert.equal(verdict(call("contratto_ricerca"), { ...base, permanent: "current" }).status, "no");
  assert.equal(verdict(call("rtt"), { ...base, permanent: "former" }).status, "no");
  assert.equal(verdict(call("contratto_ricerca"), { ...base, permanent: "former" }).status, "yes");
});
test("PhD timing", () => {
  const exp = { ...base, phd: "expected", phdDate: "2027-02" };
  assert.equal(verdict(call("incarico_postdoc"), exp).status, "no");
  assert.equal(verdict(call("contratto_ricerca"), exp).status, "flag"); // within 6 months
  assert.equal(verdict(call("rtt"), { ...base, phd: "expected", phdDate: "2026-10" }).status, "yes");
});
test("foreign PhD: RTT needs Italian recognition, pre-tenure only a note", () => {
  const us = { ...base, degreeCountry: "noneu" };
  const r = verdict(call("rtt"), us);
  assert.equal(r.status, "flag");
  assert.ok(r.flags.includes("Italian recognition required"));
  const c = verdict(call("contratto_ricerca"), us);
  assert.equal(c.status, "yes");
  assert.ok(c.notes.some((x) => /committee/.test(x)));
});
test("reserved RTT call flagged", () => {
  assert.ok(verdict(call("rtt", { title: "RTT art. 24 comma 1-bis" }), base).flags.some((f) => /Reserved/.test(f)));
});
test("professors: ASN, rank, foreign post", () => {
  const pa = call("prof_associato"), po = call("prof_ordinario");
  assert.equal(verdict(pa, base).status, "no");
  assert.equal(verdict(pa, { ...base, asnII: "13/econ-04, 14/GSPS-02" }).status, "yes");
  assert.equal(verdict(po, { ...base, asnII: "13/ECON-04" }).status, "no");
  assert.equal(verdict(pa, { ...base, asnI: "13/ECON-04" }).status, "yes");
  assert.equal(verdict(pa, { ...base, rank: "associate" }).status, "yes");
  assert.equal(verdict(pa, { ...base, foreignRank: "associate" }).status, "flag");
  assert.equal(verdict(po, { ...base, foreignRank: "associate" }).status, "no");
});
test("research body post-doc without PhD: flag not block", () => {
  const p = { ...base, phd: "none" };
  assert.equal(verdict(call("incarico_postdoc", { institution: "Istituto Nazionale di Fisica Nucleare" }), p).status, "flag");
  assert.equal(verdict(call("incarico_postdoc"), p).status, "no");
});
test("experience note never changes status", () => {
  const v = verdict(call("rtt", { target_group: "More Experienced researcher or >10 yrs (Senior)" }), base);
  assert.equal(v.status, "yes");
  assert.match(v.notes[0], /over 10/);
});
test("PhD enrolment never blocks an application", () => {
  const student = { ...base, lmDate: "2023-07", phd: "expected", phdDate: "2027-10", enrolled: true };
  assert.equal(verdict(call("incarico_ricerca"), student).status, "yes");
});
