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
    };
  }

  // Backward compatibility for older snapshots.
  if (typeof state.offseasonDev.busy !== "boolean") state.offseasonDev.busy = false;
  if (state.offseasonDev.draftCombineResult === undefined) state.offseasonDev.draftCombineResult = null;
  if (state.offseasonDev.draftBundleResult === undefined) state.offseasonDev.draftBundleResult = null;
  if (!Array.isArray(state.offseasonDev.combineCategoryCards)) state.offseasonDev.combineCategoryCards = [];
  if (typeof state.offseasonDev.combineSelectedCategory !== "string") state.offseasonDev.combineSelectedCategory = "";
  if (!Array.isArray(state.offseasonDev.combineDetailRows)) state.offseasonDev.combineDetailRows = [];

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
    setOffseasonDevNextButton({ label: "완료", disabled: true });
    return;
  }

  if (step === "COMBINE_DETAIL") {
    setOffseasonDevStatus("컴바인 상세 결과입니다.");
    setOffseasonDevContentHtml(renderCombineDetailTable());
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

async function runCombineAndLoadBundleStep() {
  const flow = ensureOffseasonDevState();
  flow.draftCombineResult = await fetchJson("/api/offseason/draft/combine", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  flow.draftBundleResult = await fetchJson("/api/offseason/draft/bundle");

  const combineRows = extractCombineRowsFromBundle(flow.draftBundleResult);
  flow.combineCategoryCards = buildCombineCategoryCards(combineRows, combineCategorySpecs());
  flow.combineSelectedCategory = "";
  flow.combineDetailRows = [];
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
};
