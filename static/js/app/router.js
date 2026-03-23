import { els } from "./dom.js";
import { state } from "./state.js";
import { abortAllMarketTradeRequests } from "../core/api.js";

function activateScreen(target) {
  const wasMarketActive = Boolean(state.marketScreenActive);
  [
    els.startScreen,
    els.teamScreen,
    els.mainScreen,
    els.offseasonDevChampionScreen,
    els.offseasonDevFlowScreen,
    els.marketScreen,
    els.tradeLabScreen,
    els.gameResultScreen,
    els.scheduleScreen,
    els.myTeamScreen,
    els.playerDetailScreen,
    els.tacticsScreen,
    els.trainingScreen,
    els.standingsScreen,
    els.collegeScreen,
    els.collegeBigboardDetailScreen,
    els.medicalScreen,
  ].filter(Boolean).forEach((screen) => {
    const active = screen === target;
    screen.classList.toggle("active", active);
    screen.setAttribute("aria-hidden", active ? "false" : "true");
  });

  const isMarketTarget = target === els.marketScreen;
  state.marketScreenActive = isMarketTarget;
  if (wasMarketActive && !isMarketTarget) {
    abortAllMarketTradeRequests();
  }
}

function showOffseasonEntryScreen() {
  const target = els.offseasonDevChampionScreen || els.mainScreen;
  activateScreen(target);
}

function showOffseasonFlowScreen() {
  const target = els.offseasonDevFlowScreen || els.mainScreen;
  activateScreen(target);
}

export { activateScreen, showOffseasonEntryScreen, showOffseasonFlowScreen };
