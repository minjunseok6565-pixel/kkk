import { num, clamp } from "./guards.js";

function formatIsoDate(dateString) {
  const raw = String(dateString || "").slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(raw) ? raw : "YYYY-MM-DD";
}

function formatHeightIn(inches) {
  const inch = Math.max(0, Math.round(num(inches, 0)));
  const feet = Math.floor(inch / 12);
  const rem = inch % 12;
  return `${feet}'${String(rem).padStart(2, "0")}"`;
}

function formatWeightLb(lb) { return `${Math.round(num(lb, 0))} lb`; }

function formatMoney(n) {
  return `$${Math.round(num(n, 0)).toLocaleString("en-US")}`;
}

function formatPercent(value) {
  return `${Math.round(clamp(num(value, 0), 0, 1) * 100)}%`;
}

function seasonLabelByYear(year) {
  const y = Number(year);
  if (!Number.isFinite(y)) return "시즌 미정";
  const start = String(y).slice(-2);
  const end = String(y + 1).slice(-2).padStart(2, "0");
  return `${start}-${end} 시즌`;
}

function getOptionTypeLabel(optionType) {
  if (optionType === "PLAYER") return "플레이어 옵션";
  if (optionType === "TEAM") return "팀 옵션";
  return "옵션";
}

function formatWinPct(pct) {
  const v = clamp(num(pct, 0), 0, 1);
  return `WIN% ${v.toFixed(3).replace(/^0/, "")}`;
}

function dateToIso(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function parseIsoDate(iso) {
  const v = String(iso || "").slice(0, 10);
  const d = new Date(`${v}T00:00:00`);
  return Number.isNaN(d.getTime()) ? null : d;
}

function startOfWeek(date) {
  const d = new Date(date.getTime());
  const day = d.getDay();
  const diff = day === 0 ? -6 : 1 - day;
  d.setDate(d.getDate() + diff);
  return d;
}

function addDays(date, n) {
  const d = new Date(date.getTime());
  d.setDate(d.getDate() + n);
  return d;
}

function formatSignedDiff(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return "0.0";
  if (Math.abs(n) < 0.05) return "0.0";
  return `${n > 0 ? "+" : ""}${n.toFixed(1)}`;
}

function formatSignedDelta(v) {
  const n = num(v, 0);
  if (!n) return { text: '지난 7일 대비 변동 없음', cls: '' };
  return {
    text: `지난 7일 대비 ${n > 0 ? '+' : ''}${n}`,
    cls: n > 0 ? 'pos' : 'neg',
  };
}


function formatDraftRoundLabel(roundNumber) {
  const round = Number(roundNumber);
  if (round === 1) return "1st round";
  if (round === 2) return "2nd round";
  if (round === 3) return "3rd round";
  if (Number.isFinite(round) && round > 0) return `${round}th round`;
  return "round";
}

function formatPickLabel({ year, round, teamName = "", includeTeam = true } = {}) {
  const yearNum = Number(year);
  const roundLabel = formatDraftRoundLabel(round);
  const yearText = Number.isFinite(yearNum) ? String(yearNum) : "----";
  const base = `${yearText} ${roundLabel} pick`;
  const team = String(teamName || "").trim();
  if (!includeTeam || !team) return base;
  return `${base} (${team})`;
}

function formatProtectionSummary(protection) {
  if (!protection || typeof protection !== "object" || Array.isArray(protection)) return "Unprotected";
  const type = String(protection.type || "").toUpperCase();
  const value = Number(protection.value ?? protection.n ?? protection.top_n);

  if (["TOP", "TOP_N", "TOP_PROTECTED"].includes(type) && Number.isFinite(value) && value > 0) {
    return `Top ${Math.floor(value)} protected`;
  }

  if (type === "LOTTERY") return "Lottery protected";
  if (type === "UNPROTECTED") return "Unprotected";

  return type ? `${type} protected` : "Protected";
}

function formatSwapAssetLabel({ year, round, pickA = "", pickB = "" } = {}) {
  const y = Number(year);
  const yearText = Number.isFinite(y) ? String(y) : "----";
  const roundLabel = formatDraftRoundLabel(round);
  const pairText = [String(pickA || "").trim(), String(pickB || "").trim()].filter(Boolean).join(" ↔ ");
  return pairText
    ? `${yearText} ${roundLabel} swap right (${pairText})`
    : `${yearText} ${roundLabel} swap right`;
}

function formatFixedAssetLabel({ label = "", draftYear = null, sourcePickId = "", assetId = "" } = {}) {
  const cleanLabel = String(label || "").trim();
  const yr = Number(draftYear);
  const yearText = Number.isFinite(yr) ? String(yr) : "";
  const source = String(sourcePickId || "").trim();
  const fallbackId = String(assetId || "").trim();

  const bits = [cleanLabel || "Fixed asset"];
  if (yearText) bits.push(yearText);
  if (source) bits.push(`from ${source}`);
  if (!cleanLabel && fallbackId) bits.push(`#${fallbackId}`);
  return bits.join(" · ");
}
export { formatIsoDate, formatHeightIn, formatWeightLb, formatMoney, formatPercent, seasonLabelByYear, getOptionTypeLabel, formatWinPct, dateToIso, parseIsoDate, startOfWeek, addDays, formatSignedDiff, formatSignedDelta, formatDraftRoundLabel, formatPickLabel, formatProtectionSummary, formatSwapAssetLabel, formatFixedAssetLabel };
