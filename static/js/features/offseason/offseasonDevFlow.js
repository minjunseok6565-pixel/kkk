import { state } from "../../app/state.js";
import { els } from "../../app/dom.js";
import { activateScreen } from "../../app/router.js";
import { fetchJson, setLoading } from "../../core/api.js";

function ensureOffseasonDevState() {
  if (!state.offseasonDev || typeof state.offseasonDev !== "object") {
    state.offseasonDev = {
      step: "IDLE",
      loading: false,
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
    };
  }
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
  const flow = ensureOffseasonDevState();
  const lr = flow.draftLotteryResult?.plan?.lottery_result || flow.draftLotteryResult?.lottery_result || {};
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

function renderOwnershipRows() {
  const flow = ensureOffseasonDevState();
  const turns = Array.isArray(flow.draftSettleResult?.turns) ? flow.draftSettleResult.turns : [];
  const rows = turns
    .filter((t) => Number(t?.round) === 1 && Number(t?.slot) >= 1 && Number(t?.slot) <= 14)
    .sort((a, b) => Number(a?.slot || 0) - Number(b?.slot || 0));

  return renderSimpleRows(rows, (row) => `
    <div class="offseason-dev-row">
      <div>
        <strong>R1 #${String(row?.slot || "-")} (${String(row?.pick_id || "")})</strong>
        <div class="subtitle">원 소유: ${String(row?.original_team || "-")}</div>
      </div>
      <span class="subtitle">현 소유/지명: ${String(row?.drafting_team || "-")}</span>
    </div>
  `);
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
    setOffseasonDevStatus("retirement/process 완료. 다음은 로터리/정산 단계입니다.");
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
    setOffseasonDevNextButton({ label: "다음으로 (lottery/settle)", disabled: false });
    return;
  }

  if (step === "LOTTERY_SETTLED") {
    setOffseasonDevStatus("로터리 1~14 확률/1픽 확률 + 픽 소유권 상세 단계입니다.");
    setOffseasonDevContentHtml(`
      <h4>로터리 확률표 (1~14)</h4>
      ${renderLotteryOddsRows()}
      <h4>픽 소유권 상세 (R1 슬롯 1~14)</h4>
      ${renderOwnershipRows()}
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

async function runLotteryAndSettleStep() {
  const flow = ensureOffseasonDevState();
  flow.draftLotteryResult = await fetchJson("/api/offseason/draft/lottery", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  flow.draftSettleResult = await fetchJson("/api/offseason/draft/settle", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  flow.step = "LOTTERY_SETTLED";
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

async function advanceOffseasonDevStep() {
  const flow = ensureOffseasonDevState();
  const step = String(flow.step || "IDLE");

  if (step === "IDLE") return;

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
      await runLotteryAndSettleStep();
    }

    flow.error = "";
    renderOffseasonDevFlow();
  } catch (e) {
    flow.error = String(e?.message || "오프시즌 단계 진행 중 오류가 발생했습니다.");
    setOffseasonDevStatus(`오류: ${flow.error}`);
  } finally {
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
};
