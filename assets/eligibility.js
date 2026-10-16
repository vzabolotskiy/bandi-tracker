// Eligibility rules for bandi.mur.gov.it calls.
// Spec: "Eligibility rules for Italian research positions" (Oct 2026).
// verdict(call, profile) -> {status: "yes" | "flag" | "no" | "unknown", reasons, flags, notes}
//   reasons explain a "no"; flags need action but don't block; notes are context only.

export const TYPE_LABELS = {
  incarico_ricerca: "Incarico di ricerca",
  incarico_postdoc: "Incarico post-doc",
  contratto_ricerca: "Contratto di ricerca",
  assegno_ricerca: "Assegno di ricerca",
  rtt: "RTT, tenure track",
  rtd_a: "RTDa",
  rtd_b: "RTDb",
  rtd_pnrr: "RTDa (PNRR)",
  rtd_unspecified: "Fixed-term researcher",
  prof_associato: "Associate professor",
  prof_ordinario: "Full professor",
  prof_unspecified: "Professor",
  tecnologo: "Tecnologo",
};

export const TYPE_HINTS = {
  incarico_ricerca: "Entry-level research post, master's degree required",
  incarico_postdoc: "Post-doc post, 1 to 3 years",
  contratto_ricerca: "Fixed-term research employment, 2 to 5 years",
  rtt: "Six-year tenure-track post leading to associate professor",
  rtd_a: "Legacy fixed-term researcher post",
  prof_associato: "Permanent post, ASN or equivalent foreign post needed",
  prof_ordinario: "Permanent post, ASN or equivalent foreign post needed",
  tecnologo: "Research support post, requirements set by each call",
};

const OVERALL_CAP = 132; // months, art. 22-ter c. 9
const PRETENURE = new Set(["incarico_ricerca", "incarico_postdoc", "contratto_ricerca"]);
const RTD = new Set(["rtt", "rtd_a", "rtd_b", "rtd_pnrr", "rtd_unspecified"]);
const EPR_RE = /Istituto|Stazione|Consiglio|INFN|INAF|CNR|OGS|Agenzia|Ente/i;
const RESERVED_RE = /1[\s.-]*bis|riservat/i;

// "2026-10-30T12:00" or "2026-10" -> Date (UTC). null-safe.
export function toDate(s) {
  if (!s) return null;
  const m = String(s).match(/^(\d{4})-(\d{2})(?:-(\d{2}))?/);
  if (!m) return null;
  return new Date(Date.UTC(+m[1], +m[2] - 1, m[3] ? +m[3] : 1));
}

function monthsBetween(a, b) {
  return (b.getUTCFullYear() - a.getUTCFullYear()) * 12 + (b.getUTCMonth() - a.getUTCMonth());
}

const n = (x) => Math.max(0, Number(x) || 0);

export function profileIsEmpty(p) {
  return !p || (!p.lmDate && (!p.phd || p.phd === "none") && !p.asnII && !p.asnI && !p.rank && !p.foreignRank);
}

function served(p) {
  const m = p.months || {};
  const s = {
    contratti: n(m.contratti), postdoc: n(m.postdoc), incarichi: n(m.incarichi),
    rtt: n(m.rtt), rtd: n(m.rtd),
  };
  s.art24 = s.rtt + s.rtd;
  s.total = s.contratti + s.postdoc + s.incarichi + s.art24;
  return s;
}

function gsdList(s) {
  return String(s || "").toUpperCase().split(/[\s,;]+/).filter(Boolean);
}

function experienceNote(call, p, deadline) {
  // Soft signal from the Euraxess-style target group the university fills in.
  const tg = call.target_group || "";
  const lm = toDate(p.lmDate);
  if (!tg || !lm || !deadline) return null;
  const yrs = monthsBetween(lm, deadline) / 12;
  const bands = [];
  if (/0-4/.test(tg)) bands.push([0, 4]);
  if (/4-10/.test(tg)) bands.push([4, 10]);
  if (/>10/.test(tg)) bands.push([10, 99]);
  if (!bands.length) return null;
  const lo = Math.min(...bands.map((b) => b[0]));
  const hi = Math.max(...bands.map((b) => b[1]));
  const label = hi === 99 ? `over ${lo}` : `${lo}–${hi}`;
  if (yrs < lo - 0.5) return `Aimed at ${label} years of research experience; you have about ${Math.max(0, Math.round(yrs))}`;
  return null;
}

export function verdict(call, p, today = new Date()) {
  const reasons = [];
  const flags = [];
  const notes = [];
  if (profileIsEmpty(p)) return { status: "unknown", reasons, flags, notes };

  const t = call.type;
  const deadline = toDate(call.deadline) || today;
  const s = served(p);
  const lm = toDate(p.lmDate);
  const phdDate = toDate(p.phdDate);
  const hasPhd = p.phd === "obtained" && (!phdDate || phdDate <= deadline);
  const phdExpected = p.phd === "expected" && phdDate;
  const foreignDegree = p.degreeCountry === "eu" || p.degreeCountry === "noneu";

  const needPhd = () => {
    if (hasPhd) return true;
    if (phdExpected && phdDate <= deadline) return true;
    reasons.push(p.phd === "expected" ? "PhD must be awarded by the deadline" : "Requires a PhD");
    return false;
  };
  const capCheck = (key, typeCap, min, label) => {
    if (s[key] + min > typeCap) reasons.push(`Would exceed the ${typeCap / 12}-year cap for ${label}`);
    if (s.total + min > OVERALL_CAP) reasons.push("Would exceed the 11-year cap across contracts");
  };

  if (PRETENURE.has(t)) {
    if (p.permanent === "current") reasons.push("Not open to permanent university or research-body staff");
    if (s.art24 > 0) reasons.push("Not open to past RTT or RTD holders");
  }

  if (t === "incarico_ricerca") {
    if (!lm) reasons.push("Requires a master's degree (laurea magistrale)");
    else if (monthsBetween(lm, deadline) > 72) reasons.push("Master's degree must be at most 6 years old");
    if (p.enrolled) reasons.push("Not open to students enrolled in a degree or PhD");
    capCheck("incarichi", 36, 12, "incarichi di ricerca");
    if (foreignDegree) notes.push("Foreign degree: the committee assesses equivalence");
  } else if (t === "incarico_postdoc") {
    if (!hasPhd && !(phdExpected && phdDate <= deadline)) {
      if (EPR_RE.test(call.institution || "")) flags.push("Research body: a strong CV may substitute for the PhD");
      else needPhd();
    }
    if (p.enrolled) reasons.push("Not open to students enrolled in a degree or PhD");
    capCheck("postdoc", 36, 12, "incarichi post-doc");
    if (foreignDegree) notes.push("Foreign PhD: the committee assesses equivalence");
  } else if (t === "contratto_ricerca") {
    if (!hasPhd && !(phdExpected && phdDate <= deadline)) {
      if (phdExpected && monthsBetween(deadline, phdDate) <= 6)
        flags.push("PhD expected within 6 months: some universities accept final-year candidates");
      else needPhd();
    }
    capCheck("contratti", 60, 24, "contratti di ricerca");
    if (foreignDegree) notes.push("Foreign PhD: the committee assesses equivalence");
  } else if (RTD.has(t)) {
    needPhd();
    if (p.permanent === "current" || p.permanent === "former")
      reasons.push("Not open to current or former permanent professors and researchers");
    if (s.rtt >= 36) reasons.push("Not open after 3 or more years as RTT");
    if (foreignDegree) flags.push("Italian recognition required");
    if (s.total >= OVERALL_CAP) flags.push("Over 11 years of contracts: the post may be shortened");
    if (RESERVED_RE.test(call.title || "")) flags.push("Reserved call: needs 36 months of PhD or research outside the hiring university");
    if (t !== "rtt") flags.push("Legacy RTD post");
  } else if (t === "prof_associato" || t === "prof_ordinario") {
    const g = (call.gsd || "").toUpperCase();
    const asnI = gsdList(p.asnI), asnII = gsdList(p.asnII);
    const full = t === "prof_ordinario";
    const viaAsn = g && (asnI.includes(g) || (!full && asnII.includes(g)));
    const viaRank = full ? p.rank === "full" : p.rank === "associate" || p.rank === "full";
    const viaForeign = full ? p.foreignRank === "full" : p.foreignRank === "associate" || p.foreignRank === "full";
    if (!viaAsn && !viaRank) {
      if (viaForeign) flags.push("Eligible via your post abroad: check the MUR correspondence table");
      else reasons.push(`Requires the ASN for ${g || "this field"} at ${full ? "full" : "associate"} level, or an equivalent post`);
    }
  } else if (t === "tecnologo") {
    flags.push("Requirements are set by each call");
  }

  if (p.citizenship === "noneu") notes.push("Non-EU citizen: the university files a hosting agreement, allow 90 days");
  const langs = [].concat(call.languages || []).join(" ").toUpperCase();
  if (langs.includes("ITALIAN")) flags.push("Italian listed as a required language");
  const exp = experienceNote(call, p, deadline);
  if (exp) notes.push(exp);

  const status = reasons.length ? "no" : flags.length ? "flag" : "yes";
  return { status, reasons: [...new Set(reasons)], flags: [...new Set(flags)], notes };
}
