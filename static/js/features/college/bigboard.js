import { state } from "../../app/state.js";
import { els } from "../../app/dom.js";
import { activateScreen } from "../../app/router.js";
import { fetchCachedJson } from "../../core/api.js";
import { escapeHtml } from "../../core/guards.js";
import { parseSummaryTags, tierChip } from "./leaders.js";
import { switchCollegeTab } from "./collegeScreen.js";

const COLLEGE_BIGBOARD_OVERVIEW_LIMIT = 10;
const COLLEGE_BIGBOARD_OVERVIEW_TTL_MS = 15000;
const COLLEGE_BIGBOARD_DETAIL_TTL_MS = 20000;
const COLLEGE_BIGBOARD_FETCH_CONCURRENCY = 3;

function getBigboardOverviewCacheKey(expertId) {
  return `college:bigboard:overview:expert=${String(expertId || "")}:limit=${COLLEGE_BIGBOARD_OVERVIEW_LIMIT}`;
}

function getBigboardDetailCacheKey(expertId) {
  return `college:bigboard:detail:expert=${String(expertId || "")}`;
}

async function mapWithConcurrency(items, limit, mapper) {
  const rows = Array.isArray(items) ? items : [];
  if (!rows.length) return [];
  const maxConcurrent = Math.max(1, Number(limit) || 1);
  const results = new Array(rows.length);
  let cursor = 0;

  const workers = Array.from({ length: Math.min(maxConcurrent, rows.length) }, async () => {
    while (cursor < rows.length) {
      const idx = cursor;
      cursor += 1;
      results[idx] = await mapper(rows[idx], idx);
    }
  });

  await Promise.all(workers);
  return results;
}

async function fetchBigboardOverviewByExpert(expert) {
  const expertId = String(expert?.expert_id || "");
  if (!expertId) return { ok: false, expert, error: new Error("전문가 ID가 없습니다.") };
  try {
    const payload = await fetchCachedJson({
      key: getBigboardOverviewCacheKey(expertId),
      url: `/api/offseason/draft/bigboard/expert?expert_id=${encodeURIComponent(expertId)}&pool_mode=auto&limit=${COLLEGE_BIGBOARD_OVERVIEW_LIMIT}`,
      ttlMs: COLLEGE_BIGBOARD_OVERVIEW_TTL_MS,
      staleWhileRevalidate: true,
    });
    const board = payload?.board || [];
    return { ok: true, expert, board };
  } catch (error) {
    return { ok: false, expert, error };
  }
}

async function loadCollegeBigboard() {
  if (!state.collegeExperts.length) {
    if (els.collegeBigboardSummary) els.collegeBigboardSummary.textContent = "전문가 목록이 없습니다.";
    if (els.collegeBigboardEmpty) els.collegeBigboardEmpty.classList.remove("hidden");
    if (els.collegeBigboardOverview) els.collegeBigboardOverview.innerHTML = "";
    return;
  }

  if (els.collegeBigboardLoading) els.collegeBigboardLoading.classList.remove("hidden");
  if (els.collegeBigboardError) {
    els.collegeBigboardError.classList.add("hidden");
    els.collegeBigboardError.textContent = "";
  }
  if (els.collegeBigboardEmpty) els.collegeBigboardEmpty.classList.add("hidden");

  try {
    const overviewPayloads = await mapWithConcurrency(
      state.collegeExperts,
      COLLEGE_BIGBOARD_FETCH_CONCURRENCY,
      (expert) => fetchBigboardOverviewByExpert(expert),
    );
    state.collegeBigboardOverview = overviewPayloads;
    renderCollegeBigboardOverview();
  } finally {
    if (els.collegeBigboardLoading) els.collegeBigboardLoading.classList.add("hidden");
  }
}

function renderCollegeBigboardOverview() {
  const rows = state.collegeBigboardOverview || [];
  const success = rows.filter((row) => row.ok);
  const failed = rows.filter((row) => !row.ok);

  if (els.collegeBigboardSummary) {
    els.collegeBigboardSummary.textContent = `전문가 ${rows.length}명 · 로드 성공 ${success.length}명${failed.length ? ` · 실패 ${failed.length}명` : ""}`;
  }

  if (els.collegeBigboardError) {
    if (failed.length) {
      const names = failed.map((row) => row.expert?.display_name || row.expert?.expert_id).join(", ");
      els.collegeBigboardError.textContent = `일부 전문가 데이터 로드에 실패했습니다: ${names}`;
      els.collegeBigboardError.classList.remove("hidden");
    } else {
      els.collegeBigboardError.classList.add("hidden");
      els.collegeBigboardError.textContent = "";
    }
  }

  if (!success.length) {
    if (els.collegeBigboardEmpty) els.collegeBigboardEmpty.classList.remove("hidden");
    if (els.collegeBigboardOverview) els.collegeBigboardOverview.innerHTML = "";
    return;
  }

  if (els.collegeBigboardEmpty) els.collegeBigboardEmpty.classList.add("hidden");
  if (els.collegeBigboardOverview) {
    els.collegeBigboardOverview.innerHTML = success.map((row) => {
      const topBoard = (row.board || []).slice(0, COLLEGE_BIGBOARD_OVERVIEW_LIMIT);
      const rowsHtml = topBoard.map((p) => `
        <tr>
          <td>${p?.rank ?? "-"}</td>
          <td>${escapeHtml(p?.name || "-")}</td>
          <td>${escapeHtml(p?.pos || "-")}</td>
        </tr>
      `).join("");
      return `
        <button type="button" class="college-bigboard-card" data-expert-id="${escapeHtml(row.expert.expert_id)}" role="listitem" aria-label="${escapeHtml(row.expert.display_name)} 상세 빅보드 보기">
          <p class="college-bigboard-card-title">
            <strong>${escapeHtml(row.expert.display_name || row.expert.expert_id)}</strong>
            <span class="college-inline-meta">Top ${COLLEGE_BIGBOARD_OVERVIEW_LIMIT}</span>
          </p>
          <table class="college-bigboard-mini-table">
            <thead><tr><th>#</th><th>선수</th><th>POS</th></tr></thead>
            <tbody>${rowsHtml || `<tr><td colspan="3">데이터 없음</td></tr>`}</tbody>
          </table>
        </button>
      `;
    }).join("");

    els.collegeBigboardOverview.querySelectorAll(".college-bigboard-card").forEach((card) => {
      card.addEventListener("click", () => {
        const expertId = card.dataset.expertId || "";
        state.collegeBigboardLastTriggerExpertId = expertId;
        showCollegeBigboardDetailScreen(expertId).catch((e) => alert(e.message));
      });
    });
  }
}

function renderCollegeBigboardDetailRows(board) {
  return board.length ? board.map((r) => {
    const { strengths, concerns } = parseSummaryTags(r?.summary || "");
    const strengthTags = strengths.map((tag) => `<span class="college-tag is-strength">${escapeHtml(tag)}</span>`).join("");
    const concernTags = concerns.map((tag) => `<span class="college-tag is-concern">${escapeHtml(tag)}</span>`).join("");
    return `
      <tr class="college-data-row">
        <td>${r?.rank ?? "-"}</td>
        <td>${escapeHtml(r?.name || "-")}</td>
        <td><span class="college-pos-chip">${escapeHtml(r?.pos || "-")}</span></td>
        <td>${tierChip(r?.tier)}</td>
        <td><div class="college-tag-wrap">${strengthTags}${concernTags || `<span class="college-tag">${escapeHtml(r?.summary || "-")}</span>`}</div></td>
      </tr>
    `;
  }).join("") : `<tr><td class="schedule-empty" colspan="5">빅보드 데이터가 없습니다.</td></tr>`;
}

async function fetchCollegeBigboardByExpert(expertId) {
  if (!expertId) return [];
  let board = state.collegeBigboardByExpert[expertId];
  if (!board) {
    const payload = await fetchCachedJson({
      key: getBigboardDetailCacheKey(expertId),
      url: `/api/offseason/draft/bigboard/expert?expert_id=${encodeURIComponent(expertId)}&pool_mode=auto`,
      ttlMs: COLLEGE_BIGBOARD_DETAIL_TTL_MS,
      staleWhileRevalidate: true,
    });
    board = payload?.board || [];
    state.collegeBigboardByExpert[expertId] = board;
  }
  return board;
}

async function showCollegeBigboardDetailScreen(expertId) {
  if (!expertId || !els.collegeBigboardDetailScreen) return;
  const expert = state.collegeExperts.find((item) => item.expert_id === expertId);
  const board = await fetchCollegeBigboardByExpert(expertId);

  const tier1 = board.filter((r) => /tier\s*1/i.test(String(r?.tier || ""))).length;
  const lottery = board.filter((r) => /lottery/i.test(String(r?.tier || ""))).length;
  if (els.collegeBigboardDetailTitle) {
    els.collegeBigboardDetailTitle.textContent = `${expert?.display_name || expertId} 상세 빅보드`;
  }
  if (els.collegeBigboardDetailSummary) {
    els.collegeBigboardDetailSummary.textContent = `Tier1 ${tier1}명 · Lottery ${lottery}명 · 전체 ${board.length}명`;
  }
  if (els.collegeBigboardDetailBody) {
    els.collegeBigboardDetailBody.innerHTML = renderCollegeBigboardDetailRows(board);
  }

  state.selectedCollegeBigboardExpertId = expertId;
  activateScreen(els.collegeBigboardDetailScreen);
  els.collegeBigboardDetailBackBtn?.focus();
}

function closeCollegeBigboardDetailScreen() {
  activateScreen(els.collegeScreen);
  switchCollegeTab("bigboard");
  const selector = state.collegeBigboardLastTriggerExpertId
    ? `.college-bigboard-card[data-expert-id="${CSS.escape(state.collegeBigboardLastTriggerExpertId)}"]`
    : ".college-bigboard-card";
  const trigger = els.collegeBigboardOverview?.querySelector(selector);
  trigger?.focus();
}

export { loadCollegeBigboard, renderCollegeBigboardOverview, renderCollegeBigboardDetailRows, fetchCollegeBigboardByExpert, showCollegeBigboardDetailScreen, closeCollegeBigboardDetailScreen };
