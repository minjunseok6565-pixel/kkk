import { els } from "./dom.js";
import { state } from "./state.js";
import { abortAllMarketTradeRequests } from "../core/api.js";

function activateScreen(target) {
  const wasMarketActive = Boolean(state.marketScreenActive);
  [
    els.startScreen,
    els.teamScreen,
    els.mainScreen,
    els.marketScreen,
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
  ].forEach((screen) => {
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

export { activateScreen };
