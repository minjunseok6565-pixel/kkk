import { els } from "./dom.js";

function activateScreen(target) {
  [
    els.startScreen,
    els.teamScreen,
    els.mainScreen,
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
}

export { activateScreen };
