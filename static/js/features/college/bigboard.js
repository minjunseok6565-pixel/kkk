import { state } from "../../app/state.js";
import { els } from "../../app/dom.js";
import { activateScreen } from "../../app/router.js";
import { fetchJson } from "../../core/api.js";
import { escapeHtml } from "../../core/guards.js";
import { parseSummaryTags, tierChip } from "./leaders.js";
import { switchCollegeTab } from "./collegeScreen.js";

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

  const overviewPayloads = await Promise.all(state.collegeExperts.map(async (expert) => {
    try {
      const payload = await fetchJson(`/api/offseason/draft/bigboard/expert?expert_id=${encodeURIComponent(expert.expert_id)}&pool_mode=auto&limit=10`);
      const board = payload?.board || [];
      return { ok: true, expert, board };
    } catch (error) {
      return { ok: false, expert, error };
    }
  }));

  state.collegeBigboardOverview = overviewPayloads;
  renderCollegeBigboardOverview();
  if (els.collegeBigboardLoading) els.collegeBigboardLoading.classList.add("hidden");
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
      const topBoard = (row.board || []).slice(0, 10);
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
            <span class="college-inline-meta">Top 10</span>
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
    const payload = await fetchJson(`/api/offseason/draft/bigboard/expert?expert_id=${encodeURIComponent(expertId)}&pool_mode=auto`);
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
