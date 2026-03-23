import { state } from "../../app/state.js";
import {
  fetchStateSummary,
  startContractNegotiation,
  submitContractNegotiationOffer,
  acceptContractNegotiationCounter,
  commitReSignOrExtend,
} from "../../core/api.js";
import { getSeasonStartYearFromSummary, buildFlatSalaryOffer } from "./offerBuilder.js";

function normalizeNegotiationState(session, extra = {}) {
  const s = session || {};
  return {
    ...(state.myTeamReSignNegotiation || {}),
    ...extra,
    player_id: extra.player_id || s.player_id || state.selectedPlayerId,
    mode: s.mode || extra.mode || null,
    session_id: s.session_id || extra.session_id || null,
    status: s.status || extra.status || null,
    phase: s.phase || extra.phase || null,
    round: Number(s.round || extra.round || 0),
    max_rounds: Number(s.max_rounds || extra.max_rounds || 0),
    valid_until: s.valid_until || extra.valid_until || null,
    last_decision: s.last_decision || extra.last_decision || null,
    last_counter: s.last_counter || extra.last_counter || null,
    agreed_offer: s.agreed_offer || extra.agreed_offer || null,
    player_position: s.player_position || extra.player_position || null,
    contract_end_start_year: Number(extra.contract_end_start_year || s.contract_end_start_year || 0),
    current_season_start_year: Number(extra.current_season_start_year || s.current_season_start_year || 0),
    info: extra.info || null,
    error: extra.error || null,
  };
}

async function startReSignNegotiation(playerId, { teamId, contractEndStartYear = 0, currentSeasonStartYear = 0 } = {}) {
  const out = await startContractNegotiation({
    teamId,
    playerId,
    mode: "RE_SIGN",
  });
  return normalizeNegotiationState(out, {
    player_id: playerId,
    contract_end_start_year: Number(contractEndStartYear || 0),
    current_season_start_year: Number(currentSeasonStartYear || 0),
    info: "재계약 협상이 시작되었습니다.",
    error: null,
  });
}

function resolveReSignOfferStartSeasonYear({ session, seasonYear }) {
  const currentSeason = Number(seasonYear || 0);
  const contractEnd = Number(session?.contract_end_start_year || 0);
  return Math.max(currentSeason + 1, contractEnd > 0 ? contractEnd + 1 : currentSeason + 1);
}

async function submitReSignOffer({ session, seasonYear, aav, years, playerId } = {}) {
  const activeSession = session || state.myTeamReSignNegotiation || {};
  if (!activeSession?.session_id) throw new Error("진행 중인 재계약 협상이 없습니다.");
  const startSeasonYear = resolveReSignOfferStartSeasonYear({
    session: activeSession,
    seasonYear,
  });
  const offer = buildFlatSalaryOffer({
    startSeasonYear,
    years,
    aav,
    minAav: 750000,
    minYears: 1,
    maxYears: 5,
  });
  const out = await submitContractNegotiationOffer({
    sessionId: activeSession.session_id,
    offer,
  });
  return {
    session: normalizeNegotiationState(out?.session, {
      player_id: playerId || activeSession.player_id,
      contract_end_start_year: Number(activeSession.contract_end_start_year || 0),
      current_season_start_year: Number(seasonYear || 0),
      last_decision: out?.decision || null,
      error: null,
    }),
    raw: out,
  };
}

async function acceptReSignCounter(sessionId, { playerId = "" } = {}) {
  const sid = String(sessionId || "").trim();
  if (!sid) throw new Error("진행 중인 재계약 협상이 없습니다.");
  const out = await acceptContractNegotiationCounter({ sessionId: sid });
  return normalizeNegotiationState(out?.session, {
    player_id: playerId || state.myTeamReSignNegotiation?.player_id || state.selectedPlayerId,
    info: "선수 카운터 오퍼를 수락했습니다.",
    error: null,
  });
}

async function commitReSign(session, { teamId, playerId } = {}) {
  const activeSession = session || state.myTeamReSignNegotiation || {};
  const sid = String(activeSession?.session_id || "").trim();
  if (!sid) throw new Error("확정 가능한 재계약 협상이 없습니다.");
  await commitReSignOrExtend({
    sessionId: sid,
    teamId,
    playerId,
  });
  return {
    ...activeSession,
    status: "CLOSED",
    phase: "ACCEPTED",
    committed: true,
    info: "재계약이 확정되었습니다.",
    error: null,
  };
}

async function resolveCurrentSeasonStartYear() {
  const summary = await fetchStateSummary().catch(() => null);
  return getSeasonStartYearFromSummary(summary, 0);
}

export {
  normalizeNegotiationState,
  startReSignNegotiation,
  submitReSignOffer,
  acceptReSignCounter,
  commitReSign,
  resolveCurrentSeasonStartYear,
};
