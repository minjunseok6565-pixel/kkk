import { els } from "./dom.js";
import { state } from "./state.js";
import { activateScreen } from "./router.js";
import { fetchJson, showConfirmModal } from "../core/api.js";
import {
  showMainScreen,
  createNewGame,
  continueGame,
  progressNextGameFromHome,
  autoAdvanceToNextGameDayFromHome,
} from "../features/main/mainScreen.js";
import { showMyTeamScreen, rerenderMyTeamBoard } from "../features/myteam/myTeamScreen.js";
import { showTacticsScreen, toggleTacticsOptions, saveTacticsDraft, hasUnsavedTacticsChanges } from "../features/tactics/tacticsScreen.js";
import { showScheduleScreen } from "../features/schedule/scheduleScreen.js";
import { showTrainingScreen } from "../features/training/trainingScreen.js";
import { showStandingsScreen } from "../features/standings/standingsScreen.js";
import { showCollegeScreen, switchCollegeTab, ensureCollegeTabData } from "../features/college/collegeScreen.js";
import { loadCollegeLeaders } from "../features/college/leaders.js";
import { closeCollegeBigboardDetailScreen } from "../features/college/bigboard.js";
import {
  setCollegeScoutingFeedback,
  openScoutPlayerModal,
  closeScoutPlayerModal,
  openScoutReportsModal,
  closeScoutReportsModal,
  renderScoutPlayerList,
  searchScoutingPlayers,
  queueScoutingPlayerSearch,
  loadCollegeScouting,
  invalidateCollegeScoutingCache,
} from "../features/college/scouting.js";
import { showMedicalScreen } from "../features/medical/medicalScreen.js";
import { renderTrainingDetail } from "../features/training/trainingDetail.js";

function bindEvents() {
  const onCollegeTabClick = (tab) => {
    switchCollegeTab(tab);
    ensureCollegeTabData(tab).catch((e) => alert(e.message));
  };

  els.newGameBtn.addEventListener("click", () => createNewGame().catch((e) => alert(e.message)));
  els.continueBtn.addEventListener("click", () => continueGame().catch((e) => alert(e.message)));
  els.myTeamBtn.addEventListener("click", () => showMyTeamScreen().catch((e) => alert(e.message)));
  els.tacticsMenuBtn.addEventListener("click", () => showTacticsScreen().catch((e) => alert(e.message)));
  els.nextGameTacticsBtn.addEventListener("click", () => showTacticsScreen().catch((e) => alert(e.message)));
  els.nextGamePlayBtn.addEventListener("click", () => progressNextGameFromHome().catch((e) => alert(e.message)));
  els.nextGameQuickBtn.addEventListener("click", () => autoAdvanceToNextGameDayFromHome().catch((e) => alert(e.message)));
  els.scheduleBtn.addEventListener("click", () => showScheduleScreen().catch((e) => alert(e.message)));
  els.scheduleBackBtn.addEventListener("click", () => showMainScreen());
  els.gameResultBackBtn?.addEventListener("click", () => showMainScreen());
  els.trainingMenuBtn.addEventListener("click", () => showTrainingScreen().catch((e) => alert(e.message)));
  els.tacticsBackBtn.addEventListener("click", async () => {
    try {
      if (hasUnsavedTacticsChanges()) {
        const shouldSave = await showConfirmModal({
          title: "저장되지 않은 전술 변경 사항",
          body: "저장하지 않은 전술 변경 사항이 있습니다. 저장 후 나가시겠습니까?",
          okLabel: "예",
          cancelLabel: "아니오",
        });
        if (shouldSave) {
          const saved = await saveTacticsDraft({ showSuccessMessage: false });
          if (!saved) return;
        }
      }
      showMainScreen();
    } catch (e) {
      alert(e.message);
    }
  });
  els.tacticsSaveBtn?.addEventListener("click", () => saveTacticsDraft({ showSuccessMessage: true }).catch((e) => alert(e.message)));
  els.tacticsOffenseBtn.addEventListener("click", () => toggleTacticsOptions("offense"));
  els.tacticsDefenseBtn.addEventListener("click", () => toggleTacticsOptions("defense"));
  els.standingsMenuBtn.addEventListener("click", () => showStandingsScreen().catch((e) => alert(e.message)));
  els.collegeMenuBtn.addEventListener("click", () => showCollegeScreen().catch((e) => alert(e.message)));
  els.medicalMenuBtn.addEventListener("click", () => showMedicalScreen().catch((e) => alert(e.message)));
  els.trainingBackBtn.addEventListener("click", () => showMainScreen());
  els.medicalBackBtn.addEventListener("click", () => showMainScreen());
  els.standingsBackBtn.addEventListener("click", () => showMainScreen());
  els.collegeBackBtn.addEventListener("click", () => showMainScreen());
  els.collegeTabTeams.addEventListener("click", () => onCollegeTabClick("teams"));
  els.collegeTabLeaders.addEventListener("click", () => onCollegeTabClick("leaders"));
  els.collegeTabBigboard.addEventListener("click", () => onCollegeTabClick("bigboard"));
  els.collegeTabScouting.addEventListener("click", () => onCollegeTabClick("scouting"));
  els.collegeLeaderSort.addEventListener("change", () => {
    state.collegeLeadersSort = els.collegeLeaderSort.value || "pts";
    loadCollegeLeaders().catch((e) => alert(e.message));
  });
  els.collegeLeaderPosFilter?.addEventListener("change", () => {
    state.collegeLeaderPosFilter = els.collegeLeaderPosFilter.value || "ALL";
    loadCollegeLeaders().catch((e) => alert(e.message));
  });
  els.collegeLeaderTeamFilter?.addEventListener("change", () => {
    state.collegeLeaderTeamFilter = els.collegeLeaderTeamFilter.value || "ALL";
    loadCollegeLeaders().catch((e) => alert(e.message));
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && els.collegeBigboardDetailScreen?.classList.contains("active")) {
      closeCollegeBigboardDetailScreen();
    }
    if (event.key === "Escape" && !els.collegeScoutPlayerModal?.classList.contains("hidden")) {
      closeScoutPlayerModal();
    }
    if (event.key === "Escape" && !els.collegeScoutReportsModal?.classList.contains("hidden")) {
      closeScoutReportsModal();
    }
  });
  els.collegeScoutCards?.addEventListener("click", async (event) => {
    const target = event.target instanceof HTMLElement ? event.target.closest("button[data-action]") : null;
    if (!target) return;
    const action = target.dataset.action;
    const scoutId = String(target.dataset.scoutId || "");
    if (!scoutId) return;
    if (action === "pick-player") {
      openScoutPlayerModal(scoutId);
      return;
    }
    if (action === "open-reports") {
      openScoutReportsModal(scoutId);
      return;
    }
  });

  els.collegeScoutReportInboxList?.addEventListener("click", (event) => {
    const target = event.target instanceof HTMLElement ? event.target.closest("button[data-action='open-reports']") : null;
    if (!target) return;
    const scoutId = String(target.dataset.scoutId || "");
    if (!scoutId) return;
    openScoutReportsModal(scoutId);
  });

  els.collegeBigboardDetailBackBtn?.addEventListener("click", () => closeCollegeBigboardDetailScreen());


  els.collegeScoutPlayerSearch?.addEventListener("input", () => {
    state.scoutingPlayerSearch = els.collegeScoutPlayerSearch.value || "";
    queueScoutingPlayerSearch();
  });

  els.collegeScoutPlayerStatus?.addEventListener("change", () => {
    state.scoutingPlayerSearchStatus = els.collegeScoutPlayerStatus.value || "ALL";
    queueScoutingPlayerSearch();
  });

  els.collegeScoutPlayerLoadMore?.addEventListener("click", () => {
    searchScoutingPlayers({ append: true }).catch((e) => {
      state.scoutingPlayerSearchError = e?.message || "선수 검색 중 오류가 발생했습니다.";
      state.scoutingPlayerSearchLoading = false;
      renderScoutPlayerList();
    });
  });

  els.collegeScoutPlayerList?.addEventListener("click", async (event) => {
    const option = event.target instanceof HTMLElement ? event.target.closest(".college-player-option") : null;
    if (!option) return;
    const playerId = String(option.dataset.playerId || "");
    const scoutId = String(state.scoutingActiveScoutId || "");
    if (!scoutId || !playerId) return;

    const scout = state.scoutingScouts.find((item) => String(item?.scout_id || "") === scoutId);
    const player = state.scoutingPlayerSearchResults.find((item) => String(item?.player_id || "") === playerId)
      || state.scoutingPlayerLookup[playerId]
      || null;
    if (String(scout?.active_assignment?.target_player_id || "") === playerId) {
      setCollegeScoutingFeedback("이미 이 선수에게 배정된 스카우터입니다.", "warn");
      closeScoutPlayerModal();
      return;
    }

    if (scout?.active_assignment?.assignment_id) {
      const ok = await showConfirmModal({
        title: "스카우팅 배정 교체",
        body: `${scout?.display_name || scoutId}의 기존 배정을 종료하고 ${player?.name || playerId}로 변경하시겠습니까?`,
        okLabel: "교체",
        cancelLabel: "취소",
      });
      if (!ok) return;
    }

    try {
      if (scout?.active_assignment?.assignment_id) {
        await fetchJson("/api/scouting/unassign", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ team_id: state.selectedTeamId, scout_id: scoutId })
        });
      }
      await fetchJson("/api/scouting/assign", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ team_id: state.selectedTeamId, scout_id: scoutId, player_id: playerId, target_kind: "COLLEGE" })
      });
      if (player && playerId) state.scoutingPlayerLookup[playerId] = player;
      invalidateCollegeScoutingCache(state.selectedTeamId);
      await loadCollegeScouting({ force: true });
      setCollegeScoutingFeedback(`${scout?.display_name || scoutId} → ${player?.name || playerId} 배정 완료`, "ok");
      closeScoutPlayerModal();
    } catch (error) {
      setCollegeScoutingFeedback(error?.message || "배정 처리 중 오류가 발생했습니다.", "warn");
    }
  });

  els.collegeScoutPlayerModalClose?.addEventListener("click", closeScoutPlayerModal);
  els.collegeScoutPlayerModalBackdrop?.addEventListener("click", closeScoutPlayerModal);
  els.collegeScoutReportsModalClose?.addEventListener("click", closeScoutReportsModal);
  els.collegeScoutReportsModalBackdrop?.addEventListener("click", closeScoutReportsModal);
  els.trainingTypeButtons.querySelectorAll("button[data-training-type]").forEach((btn) => {
    btn.addEventListener("click", () => renderTrainingDetail(btn.dataset.trainingType).catch((e) => alert(e.message)));
  });
  els.backToMainBtn.addEventListener("click", () => showMainScreen());
  els.backToRosterBtn.addEventListener("click", () => activateScreen(els.myTeamScreen));

  if (els.myTeamSortControls) {
    els.myTeamSortControls.querySelectorAll('.myteam-chip[data-sort]').forEach((btn) => {
      btn.addEventListener('click', () => {
        state.myTeamSortKey = btn.dataset.sort || 'ovr';
        rerenderMyTeamBoard();
      });
    });
  }
  if (els.myTeamFilterControls) {
    els.myTeamFilterControls.querySelectorAll('.myteam-chip[data-filter]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const key = btn.dataset.filter;
        state.myTeamFilters[key] = !state.myTeamFilters[key];
        rerenderMyTeamBoard();
      });
    });
  }
}

export { bindEvents };
