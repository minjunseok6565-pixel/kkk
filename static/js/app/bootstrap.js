import { state } from "./state.js";
import { els } from "./dom.js";
import { activateScreen } from "./router.js";
import { bindEvents } from "./events.js";
import { loadSavesStatus } from "../features/main/mainScreen.js";
import { renderRosterRows } from "../features/myteam/myTeamScreen.js";

function registerDebugHooks() {
  window.__debugRenderMyTeam = function __debugRenderMyTeam() {
    state.selectedTeamId = "BOS";
    state.selectedTeamName = "보스턴 셀틱스";
    state.rosterRows = [
      { player_id: "p1", name: "J. Tatum", pos: "SF", age: 27, height_in: 80, weight_lb: 210, salary: 34000000, short_term_stamina: 0.72, long_term_stamina: 0.86, sharpness: 89 },
      { player_id: "p2", name: "J. Brown", pos: "SG", age: 28, height_in: 78, weight_lb: 223, salary: 32000000, short_term_stamina: 0.51, long_term_stamina: 0.78, sharpness: 61 },
      { player_id: "p3", name: "K. Porzingis", pos: "C", age: 29, height_in: 87, weight_lb: 240, salary: 36000000, short_term_stamina: 0.33, long_term_stamina: 0.62, sharpness: 42 }
    ];
    els.myTeamTitle.textContent = `${state.selectedTeamName} 선수단`;
    renderRosterRows(state.rosterRows);
    els.playerDetailTitle.textContent = "선수 상세 정보";
    els.playerDetailContent.innerHTML = "";
    activateScreen(els.myTeamScreen);
  };
}

function initApp() {
  bindEvents();
  loadSavesStatus();
  registerDebugHooks();
}

export { initApp };
