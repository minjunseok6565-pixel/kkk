import { state } from "../../app/state.js";
import { els } from "../../app/dom.js";
import { activateScreen } from "../../app/router.js";
import { fetchJson, setLoading } from "../../core/api.js";

function ensureOffseasonDevState() {
  if (!state.offseasonDev || typeof state.offseasonDev !== "object") {
    state.offseasonDev = {
      step: "IDLE",
      loading: false,
      busy: false,
      error: "",
      championTeamId: "",
      enterOffseasonResult: null,
      collegeFinalizeResult: null,
      collegeTeamsResult: null,
      collegeLeadersResult: null,
      collegeDeclarersResult: null,
      pendingTeamOptions: [],
      teamOptionDecisions: {},
      contractsProcessResult: null,
      playerOptionResults: null,
      expiredContractsResult: null,
      expiredNegotiationSessions: {},
      retirementProcessResult: null,
      draftLotteryResult: null,
      draftSettleResult: null,
      draftCombineResult: null,
      draftBundleResult: null,
      combineCategoryCards: [],
      combineSelectedCategory: "",
      combineDetailRows: [],
      workoutRound: 1,
      workoutMaxRounds: 3,
      workoutInviteLimit: 10,
      workoutInvitedCurrent: [],
      workoutInvitedByRound: {},
      workoutDoneProspectIds: [],
      workoutResultsByRound: {},
      interviewQuestionCatalog: [],
      interviewSelectionsByRound: {},
      interviewResultsByRound: {},
      interviewCurrentProspectIndex: 0,
      workoutDecisionPendingRound: 0,
    };
  }

  // Backward compatibility for older snapshots.
  if (typeof state.offseasonDev.busy !== "boolean") state.offseasonDev.busy = false;
  if (state.offseasonDev.draftCombineResult === undefined) state.offseasonDev.draftCombineResult = null;
  if (state.offseasonDev.draftBundleResult === undefined) state.offseasonDev.draftBundleResult = null;
  if (!Array.isArray(state.offseasonDev.combineCategoryCards)) state.offseasonDev.combineCategoryCards = [];
  if (typeof state.offseasonDev.combineSelectedCategory !== "string") state.offseasonDev.combineSelectedCategory = "";
  if (!Array.isArray(state.offseasonDev.combineDetailRows)) state.offseasonDev.combineDetailRows = [];
  if (!Number.isFinite(Number(state.offseasonDev.workoutRound))) state.offseasonDev.workoutRound = 1;
  if (!Number.isFinite(Number(state.offseasonDev.workoutMaxRounds))) state.offseasonDev.workoutMaxRounds = 3;
  if (!Number.isFinite(Number(state.offseasonDev.workoutInviteLimit))) state.offseasonDev.workoutInviteLimit = 10;
  if (!Array.isArray(state.offseasonDev.workoutInvitedCurrent)) state.offseasonDev.workoutInvitedCurrent = [];
  if (!state.offseasonDev.workoutInvitedByRound || typeof state.offseasonDev.workoutInvitedByRound !== "object") state.offseasonDev.workoutInvitedByRound = {};
  if (!Array.isArray(state.offseasonDev.workoutDoneProspectIds)) state.offseasonDev.workoutDoneProspectIds = [];
  if (!state.offseasonDev.workoutResultsByRound || typeof state.offseasonDev.workoutResultsByRound !== "object") state.offseasonDev.workoutResultsByRound = {};
  if (!Array.isArray(state.offseasonDev.interviewQuestionCatalog)) state.offseasonDev.interviewQuestionCatalog = [];
  if (!state.offseasonDev.interviewSelectionsByRound || typeof state.offseasonDev.interviewSelectionsByRound !== "object") state.offseasonDev.interviewSelectionsByRound = {};
  if (!state.offseasonDev.interviewResultsByRound || typeof state.offseasonDev.interviewResultsByRound !== "object") state.offseasonDev.interviewResultsByRound = {};
  if (!Number.isFinite(Number(state.offseasonDev.interviewCurrentProspectIndex))) state.offseasonDev.interviewCurrentProspectIndex = 0;
  if (!Number.isFinite(Number(state.offseasonDev.workoutDecisionPendingRound))) state.offseasonDev.workoutDecisionPendingRound = 0;

  return state.offseasonDev;
}

function setOffseasonDevStatus(text = "") {
  if (els.offseasonDevStatus) els.offseasonDevStatus.textContent = String(text || "");
}

function setOffseasonDevContentHtml(html = "") {
  if (els.offseasonDevContent) els.offseasonDevContent.innerHTML = String(html || "");
}

function setOffseasonDevNextButton({ label = "다음으로", disabled = false } = {}) {
  if (!els.offseasonDevNextBtn) return;
  els.offseasonDevNextBtn.textContent = String(label || "다음으로");
  els.offseasonDevNextBtn.disabled = Boolean(disabled);
}

function normalizeDecision(value) {
  const v = String(value || "").toUpperCase();
  if (v === "DECLINE") return "DECLINE";
  return "EXERCISE";
}

function readFlowYears() {
  const flow = ensureOffseasonDevState();
  const fromYear = Number(flow.enterOffseasonResult?.from_season_year || 0);
  const toYear = Number(flow.enterOffseasonResult?.draft_year || (fromYear ? fromYear + 1 : 0));
  return { fromYear, toYear };
}

function renderSimpleRows(items = [], mapper = () => "") {
  const rows = Array.isArray(items) ? items : [];
  if (!rows.length) return `<div class="offseason-dev-list"><p>표시할 데이터가 없습니다.</p></div>`;
  return `<div class="offseason-dev-list">${rows.map(mapper).join("")}</div>`;
}

function getLotteryResult() {
  const flow = ensureOffseasonDevState();
  return flow.draftLotteryResult?.plan?.lottery_result || flow.draftLotteryResult?.lottery_result || {};
}

function getWinnersTop4() {
  const lr = getLotteryResult();
  const raw = Array.isArray(lr?.winners_top4) ? lr.winners_top4 : [];
  return raw.map((t) => String(t || "")).filter(Boolean).slice(0, 4);
}

function combineCategorySpecs() {
  return [
    { key: "vertical", label: "Vertical", sort: "desc" },
    { key: "standing_reach", label: "Standing Reach", sort: "desc" },
    { key: "sprint_3_4", label: "3/4 Sprint", sort: "asc" },
    { key: "lane_agility", label: "Lane Agility", sort: "asc" },
    { key: "shuttle", label: "Shuttle", sort: "asc" },
  ];
}

function toFiniteNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function getCombineValue(combine, categoryKey) {
  if (!combine || typeof combine !== "object") return null;
  const direct = toFiniteNumber(combine?.[categoryKey]);
  if (direct !== null) return direct;
  const m = combine?.measurements && typeof combine.measurements === "object" ? combine.measurements : null;
  const d = combine?.drills && typeof combine.drills === "object" ? combine.drills : null;
  return toFiniteNumber(m?.[categoryKey] ?? d?.[categoryKey]);
}

function extractCombineRowsFromBundle(bundle) {
  const prospects = Array.isArray(bundle?.pool?.prospects) ? bundle.pool.prospects : [];
  return prospects.map((p) => {
    const combine = p?.meta?.combine && typeof p.meta.combine === "object" ? p.meta.combine : null;
    return {
      prospectTempId: String(p?.temp_id || p?.prospect_temp_id || ""),
      name: String(p?.name || p?.display_name || p?.prospect_temp_id || "선수"),
      pos: String(p?.pos || "-"),
      team: String(p?.college_team_id || p?.school || "-"),
      combine,
    };
  });
}

function buildTop10ByCategory(rows, categorySpec) {
  const key = String(categorySpec?.key || "");
  const sortOrder = String(categorySpec?.sort || "desc").toLowerCase();
  const normalized = (Array.isArray(rows) ? rows : [])
    .map((row) => ({ ...row, value: getCombineValue(row?.combine, key) }))
    .filter((row) => row.value !== null);

  normalized.sort((a, b) => {
    if (sortOrder === "asc") return Number(a.value) - Number(b.value);
    return Number(b.value) - Number(a.value);
  });

  return normalized.slice(0, 10);
}

function buildCombineCategoryCards(rows, categorySpecs = combineCategorySpecs()) {
  return (Array.isArray(categorySpecs) ? categorySpecs : []).map((spec) => {
    const top10 = buildTop10ByCategory(rows, spec);
    return {
      key: String(spec.key || ""),
      label: String(spec.label || spec.key || "항목"),
      sort: String(spec.sort || "desc").toLowerCase(),
      top10,
    };
  });
}

function buildCombineDetailRows(rows, selectedCategory) {
  const specs = combineCategorySpecs();
  const found = specs.find((s) => String(s.key || "") === String(selectedCategory || ""));
  if (!found) return [];

  const out = (Array.isArray(rows) ? rows : [])
    .map((row) => ({ ...row, value: getCombineValue(row?.combine, found.key) }))
    .filter((row) => row.value !== null);

  out.sort((a, b) => {
    if (found.sort === "asc") return Number(a.value) - Number(b.value);
    return Number(b.value) - Number(a.value);
  });

  return out;
}

function renderTeamOptionsList(pending = []) {
  const rows = Array.isArray(pending) ? pending : [];
  if (!rows.length) {
    return `
      <div class="offseason-dev-list">
        <p>유저 팀의 PENDING TEAM 옵션이 없습니다. 다음으로 진행하면 계약 처리를 실행합니다.</p>
      </div>
    `;
  }

  return `
    <div class="offseason-dev-list">
      ${rows
        .map((row) => {
          const contractId = String(row?.contract_id || "");
          const playerName = String(row?.player_name || row?.player_id || "선수");
          const salary = Number(row?.salary || 0).toLocaleString();
          const selectedDecision = normalizeDecision(state.offseasonDev?.teamOptionDecisions?.[contractId] || "EXERCISE");
          const exClass = selectedDecision === "EXERCISE" ? "btn" : "btn btn-secondary";
          const deClass = selectedDecision === "DECLINE" ? "btn" : "btn btn-secondary";

          return `
            <div class="offseason-dev-row">
              <div>
                <strong>${playerName}</strong>
                <div class="subtitle">contract: ${contractId} · ${salary ? `$${salary}` : "salary -"}</div>
              </div>
              <div class="offseason-dev-actions">
                <button type="button" class="${exClass}" data-offseason-team-option-contract-id="${contractId}" data-offseason-team-option-decision="EXERCISE">행사</button>
                <button type="button" class="${deClass}" data-offseason-team-option-contract-id="${contractId}" data-offseason-team-option-decision="DECLINE">미행사</button>
              </div>
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderCollegeTeamRows() {
  const flow = ensureOffseasonDevState();
  const rows = flow.collegeTeamsResult?.teams || flow.collegeTeamsResult?.rows || [];
  return renderSimpleRows(rows.slice(0, 20), (row) => `
    <div class="offseason-dev-row">
      <div>
        <strong>${String(row?.display_name || row?.name || row?.college_team_id || "팀")}</strong>
        <div class="subtitle">record: ${String(row?.record || `${row?.wins ?? 0}-${row?.losses ?? 0}`)}</div>
      </div>
      <span class="subtitle">rank: ${String(row?.rank ?? row?.standing ?? "-")}</span>
    </div>
  `);
}

function renderCollegeLeadersRows() {
  const flow = ensureOffseasonDevState();
  const rows = flow.collegeLeadersResult?.players || [];
  return renderSimpleRows(rows.slice(0, 15), (row) => `
    <div class="offseason-dev-row">
      <div>
        <strong>${String(row?.name || row?.player_id || "선수")}</strong>
        <div class="subtitle">${String(row?.college_team_id || "")}</div>
      </div>
      <span class="subtitle">PTS ${Number(row?.pts || 0).toFixed(1)}</span>
    </div>
  `);
}

function renderDeclarersRows() {
  const flow = ensureOffseasonDevState();
  const rows = flow.collegeDeclarersResult?.players || [];
  return renderSimpleRows(rows.slice(0, 30), (row) => `
    <div class="offseason-dev-row">
      <div>
        <strong>${String(row?.name || row?.player_id || "선수")}</strong>
        <div class="subtitle">${String(row?.college_team_id || "")}</div>
      </div>
      <span class="subtitle">status: ${String(row?.status || "DECLARED")}</span>
    </div>
  `);
}

function renderExpiredContractsRows() {
  const flow = ensureOffseasonDevState();
  const rows = flow.expiredContractsResult?.expired_contract_players || [];
  return renderSimpleRows(rows, (row) => {
    const playerId = String(row?.player_id || "");
    const sessionId = String(flow.expiredNegotiationSessions?.[playerId] || "");
    return `
      <div class="offseason-dev-row">
        <div>
          <strong>${String(row?.player_name || row?.player_id || "선수")}</strong>
          <div class="subtitle">contract: ${String(row?.contract_id || "-")} · end=${String(row?.end_season_year || "-")} · roster=${String(row?.current_roster_team_id || "-")}</div>
          ${sessionId ? `<div class="subtitle">협상 세션: ${sessionId}</div>` : ""}
        </div>
        <div class="offseason-dev-actions">
          <button type="button" class="btn btn-secondary" data-offseason-expired-player-id="${playerId}" data-offseason-expired-action="RESIGN">재계약 제의</button>
          <button type="button" class="btn btn-secondary" data-offseason-expired-player-id="${playerId}" data-offseason-expired-action="RELEASE">방출</button>
        </div>
      </div>
    `;
  });
}

function renderLotteryOddsRows() {
  const lr = getLotteryResult();
  const seedOrder = Array.isArray(lr?.seed_order) ? lr.seed_order : [];
  const oddsByTeam = (lr?.odds_by_team && typeof lr.odds_by_team === "object") ? lr.odds_by_team : {};

  const rows = seedOrder.slice(0, 14).map((teamId, idx) => {
    const odds = Number(oddsByTeam?.[teamId] || 0);
    const firstPickProb = Math.max(0, Math.min(100, odds));
    return {
      rank: idx + 1,
      teamId: String(teamId || ""),
      lotteryOdds: odds,
      firstPickProb,
    };
  });

  return renderSimpleRows(rows, (row) => `
    <div class="offseason-dev-row">
      <div>
        <strong>${row.rank}위 · ${row.teamId}</strong>
        <div class="subtitle">로터리 확률: ${row.lotteryOdds.toFixed(2)}%</div>
      </div>
      <span class="subtitle">1픽 확률: ${row.firstPickProb.toFixed(2)}%</span>
    </div>
  `);
}

function renderLotteryIn4Rows() {
  const winners = getWinnersTop4();
  const rows = winners.map((teamId) => ({ teamId }));
  return renderSimpleRows(rows, (row) => `
    <div class="offseason-dev-row">
      <div>
        <strong>${row.teamId}</strong>
        <div class="subtitle">로터리 당첨 (in 4)</div>
      </div>
      <span class="subtitle">당첨</span>
    </div>
  `);
}

function renderLotteryTop4OrderRows() {
  const winners = getWinnersTop4();
  const rows = winners.map((teamId, idx) => ({ slot: idx + 1, teamId }));
  return renderSimpleRows(rows, (row) => `
    <div class="offseason-dev-row">
      <div>
        <strong>${row.slot}픽 · ${row.teamId}</strong>
        <div class="subtitle">로터리 상위 픽 순서 확정</div>
      </div>
      <span class="subtitle">R1 #${row.slot}</span>
    </div>
  `);
}

function renderRound1FinalOrderRows() {
  const flow = ensureOffseasonDevState();
  const turns = Array.isArray(flow.draftSettleResult?.turns) ? flow.draftSettleResult.turns : [];
  const rows = turns
    .filter((t) => Number(t?.round) === 1 && Number(t?.slot) >= 1 && Number(t?.slot) <= 30)
    .sort((a, b) => Number(a?.slot || 0) - Number(b?.slot || 0));

  return renderSimpleRows(rows, (row) => `
    <div class="offseason-dev-row">
      <div>
        <strong>R1 #${String(row?.slot || "-")} · ${String(row?.pick_id || "")}</strong>
        <div class="subtitle">원 소유: ${String(row?.original_team || "-")}</div>
      </div>
      <span class="subtitle">최종 지명권: ${String(row?.drafting_team || "-")}</span>
    </div>
  `);
}

function renderCombineOverviewCards() {
  const flow = ensureOffseasonDevState();
  const cards = Array.isArray(flow.combineCategoryCards) ? flow.combineCategoryCards : [];
  if (!cards.length) {
    return `<div class="offseason-dev-list"><p>컴바인 데이터를 찾지 못했습니다.</p></div>`;
  }

  const cardsHtml = cards.map((card) => {
    const rowsHtml = (card?.top10 || []).map((row, idx) => `
      <tr>
        <td>${idx + 1}</td>
        <td>${String(row?.name || "-")}</td>
        <td>${String(row?.pos || "-")}</td>
        <td>${Number(row?.value || 0).toFixed(2)}</td>
      </tr>
    `).join("");

    return `
      <button type="button" class="offseason-combine-card" data-offseason-combine-category="${String(card?.key || "")}">
        <p class="offseason-combine-card-title"><strong>${String(card?.label || card?.key || "항목")}</strong><span class="subtitle">Top 10</span></p>
        <table class="offseason-combine-mini-table">
          <thead><tr><th>#</th><th>선수</th><th>POS</th><th>기록</th></tr></thead>
          <tbody>${rowsHtml || `<tr><td colspan="4">데이터 없음</td></tr>`}</tbody>
        </table>
      </button>
    `;
  }).join("");

  return `<div class="offseason-combine-grid">${cardsHtml}</div>`;
}

function renderCombineDetailTable() {
  const flow = ensureOffseasonDevState();
  const key = String(flow.combineSelectedCategory || "");
  const spec = combineCategorySpecs().find((s) => s.key === key);
  const label = spec?.label || key || "항목";
  const rows = Array.isArray(flow.combineDetailRows) ? flow.combineDetailRows : [];

  if (!rows.length) {
    return `
      <div class="offseason-dev-list">
        <div class="offseason-dev-row"><strong>${label} 상세</strong><span class="subtitle">데이터 없음</span></div>
        <div class="offseason-dev-actions"><button type="button" class="btn btn-secondary" data-offseason-combine-back="1">목록으로</button></div>
      </div>
    `;
  }

  const body = rows.map((row, idx) => `
    <tr>
      <td>${idx + 1}</td>
      <td>${String(row?.name || "-")}</td>
      <td>${String(row?.pos || "-")}</td>
      <td>${String(row?.team || "-")}</td>
      <td>${Number(row?.value || 0).toFixed(2)}</td>
    </tr>
  `).join("");

  return `
    <div class="offseason-dev-list">
      <div class="offseason-dev-row">
        <strong>${label} 전체 결과</strong>
        <button type="button" class="btn btn-secondary" data-offseason-combine-back="1">목록으로</button>
      </div>
      <div class="offseason-combine-detail-wrap">
        <table class="offseason-combine-detail-table">
          <thead><tr><th>#</th><th>선수</th><th>POS</th><th>학교</th><th>기록</th></tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>
    </div>
  `;
}

function loadWorkoutProspectsFromBundle() {
  const flow = ensureOffseasonDevState();
  const prospects = Array.isArray(flow.draftBundleResult?.pool?.prospects) ? flow.draftBundleResult.pool.prospects : [];
  return prospects.map((p) => ({
    prospectTempId: String(p?.temp_id || p?.prospect_temp_id || ""),
    name: String(p?.name || "선수"),
    pos: String(p?.pos || "-"),
    team: String(p?.college?.college_team_name || p?.college?.college_team_id || p?.college_team_id || "-"),
    workout: p?.workout?.result && typeof p.workout.result === "object" ? p.workout.result : null,
    interview: p?.interview?.result && typeof p.interview.result === "object" ? p.interview.result : null,
  })).filter((row) => row.prospectTempId);
}

function getCurrentWorkoutRound() {
  const flow = ensureOffseasonDevState();
  const round = Number(flow.workoutRound || 1);
  return Math.max(1, Math.min(Number(flow.workoutMaxRounds || 3), Number.isFinite(round) ? round : 1));
}

function readRoundInvites(round) {
  const flow = ensureOffseasonDevState();
  const key = String(round);
  return Array.isArray(flow.workoutInvitedByRound?.[key]) ? flow.workoutInvitedByRound[key] : [];
}

function renderWorkoutInviteSelect() {
  const flow = ensureOffseasonDevState();
  const round = getCurrentWorkoutRound();
  const inviteLimit = Number(flow.workoutInviteLimit || 10);
  const selected = Array.isArray(flow.workoutInvitedCurrent) ? flow.workoutInvitedCurrent : [];
  const done = new Set(Array.isArray(flow.workoutDoneProspectIds) ? flow.workoutDoneProspectIds.map((id) => String(id || "")) : []);
  const candidates = loadWorkoutProspectsFromBundle().filter((row) => !done.has(row.prospectTempId));

  if (!candidates.length) {
    return `<div class="offseason-dev-list"><p>추가로 초청 가능한 선수가 없습니다. 다음 단계로 진행하세요.</p></div>`;
  }

  const rows = candidates.slice(0, 120).map((row) => {
    const chosen = selected.includes(row.prospectTempId);
    const cls = chosen ? "btn" : "btn btn-secondary";
    return `
      <div class="offseason-dev-row">
        <div>
          <strong>${row.name}</strong>
          <div class="subtitle">${row.pos} · ${row.team}</div>
        </div>
        <button type="button" class="${cls}" data-offseason-workout-toggle="${row.prospectTempId}">${chosen ? "선택됨" : "선택"}</button>
      </div>
    `;
  }).join("");

  return `
    <div class="offseason-dev-list">
      <div class="offseason-dev-row">
        <strong>워크아웃 ${round}회차 초청</strong>
        <span class="subtitle">${selected.length} / ${inviteLimit}</span>
      </div>
      ${rows}
      <div class="offseason-dev-actions">
        <button type="button" class="btn" data-offseason-workout-submit="1" ${selected.length ? "" : "disabled"}>초청 완료</button>
      </div>
    </div>
  `;
}

function renderWorkoutResultsTable() {
  const flow = ensureOffseasonDevState();
  const round = getCurrentWorkoutRound();
  const invited = readRoundInvites(round);
  const byId = new Map(loadWorkoutProspectsFromBundle().map((row) => [row.prospectTempId, row]));
  const rows = invited.map((pid) => byId.get(pid)).filter(Boolean);

  const tableBody = rows.map((row) => {
    const scores = row.workout?.scores || {};
    const notes = Array.isArray(row.workout?.notes) ? row.workout.notes.slice(0, 2).join(" · ") : "-";
    return `
      <tr>
        <td>${row.name}</td><td>${row.pos}</td>
        <td>${Number(scores?.overall || 0).toFixed(1)}</td>
        <td>${Number(scores?.shooting || 0).toFixed(1)}</td>
        <td>${Number(scores?.ball_skills || 0).toFixed(1)}</td>
        <td>${Number(scores?.defense || 0).toFixed(1)}</td>
        <td>${Number(scores?.medical_risk || 0).toFixed(1)}</td>
        <td>${notes}</td>
      </tr>
    `;
  }).join("");

  return `
    <div class="offseason-dev-list">
      <div class="offseason-dev-row"><strong>워크아웃 ${round}회차 결과</strong><span class="subtitle">${rows.length}명</span></div>
      <div class="offseason-combine-detail-wrap">
        <table class="offseason-combine-detail-table">
          <thead><tr><th>선수</th><th>POS</th><th>Overall</th><th>Shooting</th><th>Ball</th><th>Defense</th><th>Medical</th><th>비고</th></tr></thead>
          <tbody>${tableBody || `<tr><td colspan="8">결과가 없습니다.</td></tr>`}</tbody>
        </table>
      </div>
      <div class="offseason-dev-actions">
        <button type="button" class="btn" data-offseason-workout-start-interviews="1">인터뷰</button>
      </div>
    </div>
  `;
}

function getInterviewSelectionFor(round, prospectTempId) {
  const flow = ensureOffseasonDevState();
  const rkey = String(round);
  const pkey = String(prospectTempId || "");
  const byRound = flow.interviewSelectionsByRound?.[rkey];
  if (!byRound || typeof byRound !== "object") return [];
  return Array.isArray(byRound[pkey]) ? byRound[pkey] : [];
}

function renderInterviewProgress() {
  const flow = ensureOffseasonDevState();
  const round = getCurrentWorkoutRound();
  const invited = readRoundInvites(round);
  const idx = Math.max(0, Number(flow.interviewCurrentProspectIndex || 0));
  const currentProspectId = invited[idx] || "";
  const prospectsById = new Map(loadWorkoutProspectsFromBundle().map((row) => [row.prospectTempId, row]));
  const current = prospectsById.get(currentProspectId);
  if (!currentProspectId || !current) {
    return `<div class="offseason-dev-list"><p>인터뷰 대상 선수가 없습니다.</p></div>`;
  }

  const qs = Array.isArray(flow.interviewQuestionCatalog) ? flow.interviewQuestionCatalog : [];
  const selectedQids = getInterviewSelectionFor(round, currentProspectId);
  const qHtml = qs.map((q) => {
    const qid = String(q?.id || "");
    const selected = selectedQids.includes(qid);
    return `
      <button type="button" class="${selected ? "btn" : "btn btn-secondary"}" data-offseason-interview-question="${qid}">
        ${String(q?.question || qid || "질문")}
      </button>
    `;
  }).join("");

  return `
    <div class="offseason-dev-list">
      <div class="offseason-dev-row">
        <strong>인터뷰 ${round}회차 · ${idx + 1}/${invited.length}</strong>
        <span class="subtitle">${current.name} (${current.pos})</span>
      </div>
      <p class="subtitle">질문 3개를 선택 후 제출하세요. (현재 ${selectedQids.length}/3)</p>
      <div class="offseason-dev-actions">${qHtml}</div>
      <div class="offseason-dev-actions">
        <button type="button" class="btn" data-offseason-interview-submit="1" ${selectedQids.length === 3 ? "" : "disabled"}>질문 제출</button>
      </div>
    </div>
  `;
}

function renderRoundCompleteDecision() {
  const flow = ensureOffseasonDevState();
  const round = Number(flow.workoutDecisionPendingRound || getCurrentWorkoutRound());
  const canContinue = round < Number(flow.workoutMaxRounds || 3);
  return `
    <div class="offseason-dev-list">
      <div class="offseason-dev-row"><strong>워크아웃이 종료되었습니다.</strong><span class="subtitle">${round}회차 완료</span></div>
      <div class="offseason-dev-actions">
        ${canContinue ? `<button type="button" class="btn btn-secondary" data-offseason-workout-continue-round="1">워크아웃 추가 진행</button>` : ""}
        <button type="button" class="btn" data-offseason-workout-proceed-next="1">다음 단계로 이동</button>
      </div>
    </div>
  `;
}

function renderOffseasonDevFlow() {
  const flow = ensureOffseasonDevState();
  const step = String(flow.step || "IDLE");

  if (step === "ENTERED_OFFSEASON") {
    setOffseasonDevStatus("오프시즌에 진입했습니다. 다음으로 대학 시즌 마감/선언자 생성을 실행합니다.");
    setOffseasonDevContentHtml(`
      <div class="offseason-dev-list">
        <div class="offseason-dev-row">
          <strong>진입 완료</strong>
          <span class="subtitle">/api/season/enter-offseason 완료</span>
        </div>
      </div>
    `);
    setOffseasonDevNextButton({ label: "다음으로 (대학 finalize)", disabled: false });
    return;
  }

  if (step === "COLLEGE_FINALIZED") {
    const draftYear = flow.collegeFinalizeResult?.draft_year;
    setOffseasonDevStatus("대학 시즌 마감이 완료되었습니다. 다음으로 대학 순위/리더보드를 불러옵니다.");
    setOffseasonDevContentHtml(`
      <div class="offseason-dev-list">
        <div class="offseason-dev-row">
          <strong>대학 finalize 완료</strong>
          <span class="subtitle">draft_year: ${draftYear || "-"}</span>
        </div>
      </div>
    `);
    setOffseasonDevNextButton({ label: "다음으로 (대학 순위/리더보드)", disabled: false });
    return;
  }

  if (step === "COLLEGE_RESULTS_LOADED") {
    setOffseasonDevStatus("대학 순위/리더보드 단계입니다. 다음으로 선언자 목록으로 이동합니다.");
    setOffseasonDevContentHtml(`
      <h4>대학 팀 순위 (상위)</h4>
      ${renderCollegeTeamRows()}
      <h4>대학 리더보드 (PTS)</h4>
      ${renderCollegeLeadersRows()}
    `);
    setOffseasonDevNextButton({ label: "다음으로 (드래프트 선언자)", disabled: false });
    return;
  }

  if (step === "DECLARERS_LOADED") {
    setOffseasonDevStatus("최종 드래프트 선언자 목록입니다. 다음으로 TEAM 옵션 결정을 진행합니다.");
    setOffseasonDevContentHtml(renderDeclarersRows());
    setOffseasonDevNextButton({ label: "다음으로 (TEAM 옵션)", disabled: false });
    return;
  }

  if (step === "TEAM_OPTIONS") {
    const pending = Array.isArray(flow.pendingTeamOptions) ? flow.pendingTeamOptions : [];
    setOffseasonDevStatus("TEAM 옵션을 결정한 뒤 다음으로 계약 처리를 진행하세요.");
    setOffseasonDevContentHtml(renderTeamOptionsList(pending));
    setOffseasonDevNextButton({ label: "다음으로 (contracts/process)", disabled: false });
    return;
  }

  if (step === "CONTRACTS_PROCESSED") {
    const result = flow.contractsProcessResult?.result || {};
    const expired = Number(result?.expired || 0);
    const released = Number(result?.released || 0);
    setOffseasonDevStatus("contracts/process 완료. 다음은 PLAYER 옵션 결과를 로드합니다.");
    setOffseasonDevContentHtml(`
      <div class="offseason-dev-list">
        <div class="offseason-dev-row"><strong>contracts/process 완료</strong><span class="subtitle">expired=${expired}, released=${released}</span></div>
      </div>
    `);
    setOffseasonDevNextButton({ label: "다음으로 (PLAYER 옵션 결과)", disabled: false });
    return;
  }

  if (step === "PLAYER_OPTIONS_LOADED") {
    const rows = flow.playerOptionResults?.player_option_results || [];
    setOffseasonDevStatus("선수 옵션 결과 단계입니다 (유저 결정 없음). 다음으로 만료 계약자 단계로 이동합니다.");
    setOffseasonDevContentHtml(
      renderSimpleRows(rows, (row) => `
        <div class="offseason-dev-row">
          <div>
            <strong>${String(row?.player_name || row?.player_id || "선수")}</strong>
            <div class="subtitle">${String(row?.option_type || "PLAYER")} · ${String(row?.option_status || "-")} · ${String(row?.expected_effect || "-")}</div>
          </div>
          <span class="subtitle">현재: ${String(row?.current_roster_team_id || "-")}</span>
        </div>
      `)
    );
    setOffseasonDevNextButton({ label: "다음으로 (만료 계약자)", disabled: false });
    return;
  }

  if (step === "EXPIRED_CONTRACTS_LOADED") {
    setOffseasonDevStatus("만료 계약자 단계입니다. 재계약 제의/방출 버튼이 작동합니다. 다음으로 은퇴 처리로 이동합니다.");
    setOffseasonDevContentHtml(renderExpiredContractsRows());
    setOffseasonDevNextButton({ label: "다음으로 (은퇴 처리)", disabled: false });
    return;
  }

  if (step === "RETIREMENT_PROCESSED") {
    const retiredRows = flow.retirementProcessResult?.retired_players || flow.retirementProcessResult?.players || [];
    setOffseasonDevStatus("retirement/process 완료. 다음은 로터리 확률 공개 단계입니다.");
    setOffseasonDevContentHtml(
      renderSimpleRows(retiredRows, (row) => `
        <div class="offseason-dev-row">
          <div>
            <strong>${String(row?.player_name || row?.name || row?.player_id || "선수")}</strong>
            <div class="subtitle">${String(row?.reason || "RETIRED")}</div>
          </div>
          <span class="subtitle">retired</span>
        </div>
      `)
    );
    setOffseasonDevNextButton({ label: "다음으로 (로터리 확률)", disabled: false });
    return;
  }

  if (step === "LOTTERY_ODDS") {
    setOffseasonDevStatus("로터리 확률표 단계입니다. 다음으로 in 4 당첨팀을 공개합니다.");
    setOffseasonDevContentHtml(`
      <h4>로터리 확률표 (1~14)</h4>
      ${renderLotteryOddsRows()}
    `);
    setOffseasonDevNextButton({ label: "다음으로 (로터리 in 4 공개)", disabled: false });
    return;
  }

  if (step === "LOTTERY_IN4_REVEALED") {
    setOffseasonDevStatus("로터리 당첨팀(in 4) 공개 단계입니다. 다음으로 1~4픽 순서를 공개합니다.");
    setOffseasonDevContentHtml(`
      <h4>로터리 당첨팀 (in 4)</h4>
      ${renderLotteryIn4Rows()}
    `);
    setOffseasonDevNextButton({ label: "다음으로 (1~4픽 공개)", disabled: false });
    return;
  }

  if (step === "LOTTERY_TOP4_ORDER_REVEALED") {
    setOffseasonDevStatus("1~4픽 순서 공개 단계입니다. 다음으로 보호/스왑 정산 후 1~30픽을 공개합니다.");
    setOffseasonDevContentHtml(`
      <h4>로터리 1~4픽 순서</h4>
      ${renderLotteryTop4OrderRows()}
    `);
    setOffseasonDevNextButton({ label: "다음으로 (보호/스왑 반영 후 1~30픽)", disabled: false });
    return;
  }

  if (step === "DRAFT_SETTLED_ROUND1") {
    setOffseasonDevStatus("보호/스왑 반영이 완료되었습니다. 최종 1라운드(1~30픽) 순서입니다.");
    setOffseasonDevContentHtml(`
      <h4>최종 1라운드 순서 (1~30)</h4>
      ${renderRound1FinalOrderRows()}
    `);
    setOffseasonDevNextButton({ label: "다음으로 (컴바인)", disabled: false });
    return;
  }

  if (step === "COMBINE_OVERVIEW") {
    setOffseasonDevStatus("컴바인 항목별 Top 10 카드입니다. 카드를 누르면 전체 결과를 봅니다.");
    setOffseasonDevContentHtml(renderCombineOverviewCards());
    setOffseasonDevNextButton({ label: "다음으로 (워크아웃 1회차)", disabled: false });
    return;
  }

  if (step === "COMBINE_DETAIL") {
    setOffseasonDevStatus("컴바인 상세 결과입니다.");
    setOffseasonDevContentHtml(renderCombineDetailTable());
    setOffseasonDevNextButton({ label: "다음으로 (워크아웃 1회차)", disabled: false });
    return;
  }

  if (step === "WORKOUT_INVITE_SELECT") {
    const round = getCurrentWorkoutRound();
    setOffseasonDevStatus(`워크아웃 ${round}회차입니다. 최대 10명을 초청하세요.`);
    setOffseasonDevContentHtml(renderWorkoutInviteSelect());
    setOffseasonDevNextButton({ label: "초청 완료 버튼으로 진행", disabled: true });
    return;
  }

  if (step === "WORKOUT_RESULT") {
    const round = getCurrentWorkoutRound();
    setOffseasonDevStatus(`워크아웃 ${round}회차 결과입니다. 인터뷰를 진행하세요.`);
    setOffseasonDevContentHtml(renderWorkoutResultsTable());
    setOffseasonDevNextButton({ label: "인터뷰 버튼으로 진행", disabled: true });
    return;
  }

  if (step === "INTERVIEW_PROGRESS") {
    const round = getCurrentWorkoutRound();
    setOffseasonDevStatus(`인터뷰 ${round}회차 진행 중입니다. 선수당 질문 3개를 선택하세요.`);
    setOffseasonDevContentHtml(renderInterviewProgress());
    setOffseasonDevNextButton({ label: "인터뷰 화면에서 진행", disabled: true });
    return;
  }

  if (step === "ROUND_COMPLETE") {
    setOffseasonDevStatus("이번 회차 워크아웃+인터뷰가 완료되었습니다.");
    setOffseasonDevContentHtml(renderRoundCompleteDecision());
    setOffseasonDevNextButton({ label: "버튼으로 분기 선택", disabled: true });
    return;
  }

  if (step === "WITHDRAWALS_PROCESSED") {
    setOffseasonDevStatus("드래프트 철회(withdrawals) 처리가 완료되었습니다.");
    const result = flow.withdrawalsResult || {};
    setOffseasonDevContentHtml(`
      <div class="offseason-dev-list">
        <div class="offseason-dev-row"><strong>withdrawals 완료</strong><span class="subtitle">status: ${String(result?.ok ? "ok" : "done")}</span></div>
      </div>
    `);
    setOffseasonDevNextButton({ label: "완료", disabled: true });
    return;
  }

  setOffseasonDevStatus("오프시즌 흐름 준비 전입니다.");
  setOffseasonDevContentHtml("");
  setOffseasonDevNextButton({ label: "다음으로", disabled: true });
}

async function enterOffseasonFromChampionScreen() {
  if (!state.selectedTeamId) throw new Error("먼저 팀을 선택해주세요.");
  const flow = ensureOffseasonDevState();

  setLoading(true, "오프시즌 진입 중...");
  try {
    const enterResult = await fetchJson("/api/season/enter-offseason", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });

    flow.enterOffseasonResult = enterResult;
    flow.step = "ENTERED_OFFSEASON";
    flow.error = "";

    activateScreen(els.offseasonDevFlowScreen);
    renderOffseasonDevFlow();
  } finally {
    setLoading(false);
  }
}

async function loadPendingTeamOptionsStep() {
  const flow = ensureOffseasonDevState();
  const pendingResult = await fetchJson("/api/offseason/options/team/pending", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_team_id: state.selectedTeamId }),
  });

  const pending = Array.isArray(pendingResult?.pending_team_options) ? pendingResult.pending_team_options : [];
  flow.pendingTeamOptions = pending;

  const decisions = {};
  pending.forEach((item) => {
    const cid = String(item?.contract_id || "");
    if (!cid) return;
    decisions[cid] = normalizeDecision(flow.teamOptionDecisions?.[cid] || "EXERCISE");
  });
  flow.teamOptionDecisions = decisions;
  flow.step = "TEAM_OPTIONS";
}

async function applyTeamOptionsAndContractsProcess() {
  const flow = ensureOffseasonDevState();
  const pending = Array.isArray(flow.pendingTeamOptions) ? flow.pendingTeamOptions : [];

  if (pending.length) {
    const decisionsPayload = pending.map((item) => {
      const contractId = String(item?.contract_id || "");
      const decision = normalizeDecision(flow.teamOptionDecisions?.[contractId] || "EXERCISE");
      return { contract_id: contractId, decision };
    });

    await fetchJson("/api/offseason/options/team/decide", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_team_id: state.selectedTeamId,
        decisions: decisionsPayload,
      }),
    });
  }

  const contractsResult = await fetchJson("/api/offseason/contracts/process", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_team_id: state.selectedTeamId }),
  });

  flow.contractsProcessResult = contractsResult;
  flow.step = "CONTRACTS_PROCESSED";
}

async function loadCollegeResultsStep() {
  const flow = ensureOffseasonDevState();
  const { fromYear } = readFlowYears();
  flow.collegeTeamsResult = await fetchJson(`/api/college/teams?season_year=${encodeURIComponent(fromYear)}`);
  flow.collegeLeadersResult = await fetchJson(`/api/college/players?season_year=${encodeURIComponent(fromYear)}&sort=pts&order=desc&limit=15`);
  flow.step = "COLLEGE_RESULTS_LOADED";
}

async function loadDeclarersStep() {
  const flow = ensureOffseasonDevState();
  const { toYear } = readFlowYears();
  flow.collegeDeclarersResult = await fetchJson(`/api/college/players?draft_year=${encodeURIComponent(toYear)}&declared_only=true&sort=pts&order=desc&limit=50`);
  await loadPendingTeamOptionsStep();
  flow.step = "DECLARERS_LOADED";
}

async function loadPlayerOptionResultsStep() {
  const flow = ensureOffseasonDevState();
  flow.playerOptionResults = await fetchJson("/api/offseason/options/player/results", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ team_id: state.selectedTeamId, include_pending: true }),
  });
  flow.step = "PLAYER_OPTIONS_LOADED";
}

async function loadExpiredContractsStep() {
  const flow = ensureOffseasonDevState();
  flow.expiredContractsResult = await fetchJson("/api/offseason/contracts/expired", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ team_id: state.selectedTeamId }),
  });
  flow.step = "EXPIRED_CONTRACTS_LOADED";
}

async function processRetirementStep() {
  const flow = ensureOffseasonDevState();
  flow.retirementProcessResult = await fetchJson("/api/offseason/retirement/process", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  flow.step = "RETIREMENT_PROCESSED";
}

async function runLotteryStepOnly() {
  const flow = ensureOffseasonDevState();
  flow.draftLotteryResult = await fetchJson("/api/offseason/draft/lottery", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  flow.step = "LOTTERY_ODDS";
}

async function runSettleStepOnly() {
  const flow = ensureOffseasonDevState();
  flow.draftSettleResult = await fetchJson("/api/offseason/draft/settle", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  flow.step = "DRAFT_SETTLED_ROUND1";
}

async function loadDraftBundleForViewer() {
  const flow = ensureOffseasonDevState();
  const teamId = String(state.selectedTeamId || "").trim();
  const q = teamId ? `?viewer_team_id=${encodeURIComponent(teamId)}` : "";
  flow.draftBundleResult = await fetchJson(`/api/offseason/draft/bundle${q}`);
  return flow.draftBundleResult;
}

function resetWorkoutInterviewFlowState() {
  const flow = ensureOffseasonDevState();
  flow.workoutRound = 1;
  flow.workoutInvitedCurrent = [];
  flow.workoutInvitedByRound = {};
  flow.workoutDoneProspectIds = [];
  flow.workoutResultsByRound = {};
  flow.interviewQuestionCatalog = [];
  flow.interviewSelectionsByRound = {};
  flow.interviewResultsByRound = {};
  flow.interviewCurrentProspectIndex = 0;
  flow.workoutDecisionPendingRound = 0;
  flow.withdrawalsResult = null;
}

async function runCombineAndLoadBundleStep() {
  const flow = ensureOffseasonDevState();
  flow.draftCombineResult = await fetchJson("/api/offseason/draft/combine", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  await loadDraftBundleForViewer();

  const combineRows = extractCombineRowsFromBundle(flow.draftBundleResult);
  flow.combineCategoryCards = buildCombineCategoryCards(combineRows, combineCategorySpecs());
  flow.combineSelectedCategory = "";
  flow.combineDetailRows = [];
  resetWorkoutInterviewFlowState();
  flow.step = "COMBINE_OVERVIEW";
}

async function handleExpiredContractAction(playerId, action) {
  const flow = ensureOffseasonDevState();
  const pid = String(playerId || "");
  const act = String(action || "").toUpperCase();
  if (!pid || !act) return;

  setLoading(true, "만료 계약자 액션 처리 중...");
  try {
    if (act === "RESIGN") {
      const out = await fetchJson("/api/contracts/negotiation/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          team_id: state.selectedTeamId,
          player_id: pid,
          mode: "RE_SIGN",
          valid_days: 7,
        }),
      });
      flow.expiredNegotiationSessions[pid] = String(out?.session_id || "");
      setOffseasonDevStatus(`재계약 협상 진입: ${pid}`);
    } else if (act === "RELEASE") {
      await fetchJson("/api/contracts/release-to-fa", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ player_id: pid }),
      });
      setOffseasonDevStatus(`방출 완료: ${pid} (FA 전환)`);
      await loadExpiredContractsStep();
    }

    renderOffseasonDevFlow();
  } finally {
    setLoading(false);
  }
}

function handleCombineCategoryClick(categoryKey) {
  const flow = ensureOffseasonDevState();
  const key = String(categoryKey || "");
  if (!key) return;

  const combineRows = extractCombineRowsFromBundle(flow.draftBundleResult);
  flow.combineSelectedCategory = key;
  flow.combineDetailRows = buildCombineDetailRows(combineRows, key);
  flow.step = "COMBINE_DETAIL";
  renderOffseasonDevFlow();
}

function handleCombineBackToOverview() {
  const flow = ensureOffseasonDevState();
  flow.step = "COMBINE_OVERVIEW";
  renderOffseasonDevFlow();
}

function handleWorkoutInviteToggle(prospectTempId) {
  const flow = ensureOffseasonDevState();
  const pid = String(prospectTempId || "").trim();
  if (!pid) return;

  const limit = Number(flow.workoutInviteLimit || 10);
  const selected = Array.isArray(flow.workoutInvitedCurrent) ? [...flow.workoutInvitedCurrent] : [];
  const idx = selected.indexOf(pid);
  if (idx >= 0) {
    selected.splice(idx, 1);
  } else if (selected.length < limit) {
    selected.push(pid);
  }
  flow.workoutInvitedCurrent = selected;
  renderOffseasonDevFlow();
}

async function handleWorkoutInviteSubmit() {
  const flow = ensureOffseasonDevState();
  const round = getCurrentWorkoutRound();
  const invited = Array.isArray(flow.workoutInvitedCurrent) ? flow.workoutInvitedCurrent.map((id) => String(id || "")).filter(Boolean) : [];
  if (!invited.length) throw new Error("워크아웃에 초청할 선수를 선택해주세요.");

  setLoading(true, "워크아웃 실행 중...");
  try {
    const result = await fetchJson("/api/offseason/draft/workouts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        team_id: state.selectedTeamId,
        invited_prospect_temp_ids: invited,
        max_invites: Number(flow.workoutInviteLimit || 10),
      }),
    });

    flow.workoutInvitedByRound[String(round)] = invited;
    flow.workoutDoneProspectIds = Array.from(new Set([...(flow.workoutDoneProspectIds || []), ...invited]));
    flow.workoutResultsByRound[String(round)] = result;
    await loadDraftBundleForViewer();
    flow.step = "WORKOUT_RESULT";
    renderOffseasonDevFlow();
  } finally {
    setLoading(false);
  }
}

async function handleWorkoutStartInterviews() {
  const flow = ensureOffseasonDevState();
  const questionsResult = await fetchJson("/api/offseason/draft/interviews/questions");
  flow.interviewQuestionCatalog = Array.isArray(questionsResult?.questions) ? questionsResult.questions : [];
  flow.interviewCurrentProspectIndex = 0;
  flow.step = "INTERVIEW_PROGRESS";
  renderOffseasonDevFlow();
}

function handleInterviewQuestionToggle(questionId) {
  const flow = ensureOffseasonDevState();
  const round = getCurrentWorkoutRound();
  const invited = readRoundInvites(round);
  const idx = Math.max(0, Number(flow.interviewCurrentProspectIndex || 0));
  const pid = String(invited[idx] || "");
  const qid = String(questionId || "").trim();
  if (!pid || !qid) return;

  const rkey = String(round);
  if (!flow.interviewSelectionsByRound[rkey] || typeof flow.interviewSelectionsByRound[rkey] !== "object") {
    flow.interviewSelectionsByRound[rkey] = {};
  }
  const selected = Array.isArray(flow.interviewSelectionsByRound[rkey][pid]) ? [...flow.interviewSelectionsByRound[rkey][pid]] : [];
  const hitIdx = selected.indexOf(qid);
  if (hitIdx >= 0) {
    selected.splice(hitIdx, 1);
  } else if (selected.length < 3) {
    selected.push(qid);
  }
  flow.interviewSelectionsByRound[rkey][pid] = selected;
  renderOffseasonDevFlow();
}

async function handleInterviewSubmitCurrent() {
  const flow = ensureOffseasonDevState();
  const round = getCurrentWorkoutRound();
  const invited = readRoundInvites(round);
  const idx = Math.max(0, Number(flow.interviewCurrentProspectIndex || 0));
  const pid = String(invited[idx] || "");
  if (!pid) return;

  const selected = getInterviewSelectionFor(round, pid);
  if (selected.length !== 3) {
    throw new Error("선수당 질문 3개를 선택해야 합니다.");
  }

  if (idx < invited.length - 1) {
    flow.interviewCurrentProspectIndex = idx + 1;
    renderOffseasonDevFlow();
    return;
  }

  const rkey = String(round);
  const byPid = (flow.interviewSelectionsByRound?.[rkey] && typeof flow.interviewSelectionsByRound[rkey] === "object") ? flow.interviewSelectionsByRound[rkey] : {};
  const payload = invited.map((prospectTempId) => ({
    prospect_temp_id: String(prospectTempId || ""),
    selected_question_ids: Array.isArray(byPid?.[prospectTempId]) ? byPid[prospectTempId] : [],
  })).filter((row) => row.prospect_temp_id && row.selected_question_ids.length === 3);

  if (payload.length !== invited.length) {
    throw new Error("인터뷰 질문 선택이 완료되지 않은 선수가 있습니다.");
  }

  setLoading(true, "인터뷰 결과 생성 중...");
  try {
    const result = await fetchJson("/api/offseason/draft/interviews", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        team_id: state.selectedTeamId,
        interviews: payload,
      }),
    });
    flow.interviewResultsByRound[rkey] = result;
    await loadDraftBundleForViewer();
    flow.workoutDecisionPendingRound = round;
    flow.step = "ROUND_COMPLETE";
    renderOffseasonDevFlow();
  } finally {
    setLoading(false);
  }
}

function handleContinueWorkoutRound() {
  const flow = ensureOffseasonDevState();
  const maxRounds = Number(flow.workoutMaxRounds || 3);
  const nextRound = Math.min(maxRounds, getCurrentWorkoutRound() + 1);
  flow.workoutRound = nextRound;
  flow.workoutInvitedCurrent = [];
  flow.interviewCurrentProspectIndex = 0;
  flow.step = "WORKOUT_INVITE_SELECT";
  renderOffseasonDevFlow();
}

async function handleProceedToWithdrawals() {
  const flow = ensureOffseasonDevState();
  setLoading(true, "드래프트 철회 처리 중...");
  try {
    flow.withdrawalsResult = await fetchJson("/api/offseason/draft/withdrawals", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    flow.step = "WITHDRAWALS_PROCESSED";
    renderOffseasonDevFlow();
  } finally {
    setLoading(false);
  }
}

async function advanceOffseasonDevStep() {
  const flow = ensureOffseasonDevState();
  const step = String(flow.step || "IDLE");

  if (step === "IDLE") return;
  if (flow.busy) return;

  flow.busy = true;
  setLoading(true, "오프시즌 단계 진행 중...");
  try {
    if (step === "ENTERED_OFFSEASON") {
      flow.collegeFinalizeResult = await fetchJson("/api/offseason/college/finalize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      flow.step = "COLLEGE_FINALIZED";
    } else if (step === "COLLEGE_FINALIZED") {
      await loadCollegeResultsStep();
    } else if (step === "COLLEGE_RESULTS_LOADED") {
      await loadDeclarersStep();
    } else if (step === "DECLARERS_LOADED") {
      flow.step = "TEAM_OPTIONS";
    } else if (step === "TEAM_OPTIONS") {
      await applyTeamOptionsAndContractsProcess();
    } else if (step === "CONTRACTS_PROCESSED") {
      await loadPlayerOptionResultsStep();
    } else if (step === "PLAYER_OPTIONS_LOADED") {
      await loadExpiredContractsStep();
    } else if (step === "EXPIRED_CONTRACTS_LOADED") {
      await processRetirementStep();
    } else if (step === "RETIREMENT_PROCESSED") {
      await runLotteryStepOnly();
    } else if (step === "LOTTERY_ODDS") {
      flow.step = "LOTTERY_IN4_REVEALED";
    } else if (step === "LOTTERY_IN4_REVEALED") {
      flow.step = "LOTTERY_TOP4_ORDER_REVEALED";
    } else if (step === "LOTTERY_TOP4_ORDER_REVEALED") {
      await runSettleStepOnly();
    } else if (step === "DRAFT_SETTLED_ROUND1") {
      await runCombineAndLoadBundleStep();
    } else if (step === "COMBINE_OVERVIEW" || step === "COMBINE_DETAIL") {
      flow.workoutRound = 1;
      flow.workoutInvitedCurrent = [];
      flow.interviewCurrentProspectIndex = 0;
      flow.step = "WORKOUT_INVITE_SELECT";
    }

    flow.error = "";
    renderOffseasonDevFlow();
  } catch (e) {
    flow.error = String(e?.message || "오프시즌 단계 진행 중 오류가 발생했습니다.");
    setOffseasonDevStatus(`오류: ${flow.error} (현재 단계에서 다시 시도하세요)`);
  } finally {
    flow.busy = false;
    setLoading(false);
  }
}

function setTeamOptionDecision(contractId, decision) {
  const flow = ensureOffseasonDevState();
  const cid = String(contractId || "");
  if (!cid) return;
  flow.teamOptionDecisions[cid] = normalizeDecision(decision);
  renderOffseasonDevFlow();
}

export {
  enterOffseasonFromChampionScreen,
  advanceOffseasonDevStep,
  renderOffseasonDevFlow,
  setTeamOptionDecision,
  handleExpiredContractAction,
  handleCombineCategoryClick,
  handleCombineBackToOverview,
  handleWorkoutInviteToggle,
  handleWorkoutInviteSubmit,
  handleWorkoutStartInterviews,
  handleInterviewQuestionToggle,
  handleInterviewSubmitCurrent,
  handleContinueWorkoutRound,
  handleProceedToWithdrawals,
};
