function getSeasonStartYearFromSummary(summaryPayload, fallbackYear = 0) {
  const ws = summaryPayload?.workflow_state || {};
  const league = ws?.league || {};
  const activeSeasonId = String(ws?.active_season_id || "");
  const seasonIdYear = Number((activeSeasonId.match(/^(\d{4})-/) || [])[1] || 0);
  const direct = Number(league?.season_year || ws?.season_year || seasonIdYear || 0);
  if (Number.isFinite(direct) && direct > 0) return direct;
  const fallback = Number(fallbackYear || 0);
  if (Number.isFinite(fallback) && fallback > 0) return fallback;
  return 0;
}

function buildFlatSalaryOffer({ startSeasonYear, years, aav, minAav = 750000, minYears = 1, maxYears = 5 } = {}) {
  const start = Number(startSeasonYear || 0);
  const safeMinYears = Math.max(1, Number(minYears || 1));
  const safeMaxYears = Math.max(safeMinYears, Number(maxYears || 5));
  const len = Math.max(safeMinYears, Math.min(safeMaxYears, Number(years || safeMinYears)));
  const yearly = Math.max(Number(minAav || 750000), Math.round(Number(aav || 0)));
  if (!Number.isFinite(start) || start <= 0) throw new Error("시즌 시작 연도를 계산할 수 없습니다.");
  const salary_by_year = {};
  for (let i = 0; i < len; i += 1) salary_by_year[start + i] = yearly;
  return {
    start_season_year: start,
    years: len,
    salary_by_year,
    options: [],
  };
}

export { getSeasonStartYearFromSummary, buildFlatSalaryOffer };
