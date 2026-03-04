import { state } from "../../app/state.js";
import { els } from "../../app/dom.js";
import { dateToIso, parseIsoDate, startOfWeek, addDays } from "../../core/format.js";
import { trainingTypeLabel, trainingTypeIcon, buildTrainingRiskFlags, renderTrainingContextPanel, refreshTrainingTypeButtonsState } from "./trainingDetail.js";

function buildCalendar4Weeks(currentDateIso) {
  const today = parseIsoDate(currentDateIso) || new Date();
  const first = startOfWeek(today);
  const days = [];
  for (let i = 0; i < 28; i += 1) {
    const date = addDays(first, i);
    days.push(dateToIso(date));
  }
  return days;
}

function renderTrainingCalendar() {
  const container = els.trainingCalendarGrid;
  const today = state.currentDate;
  container.innerHTML = "";

  state.trainingCalendarDays.forEach((iso) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "training-day-cell";

    const d = parseIsoDate(iso);
    const label = `${d.getMonth() + 1}/${d.getDate()}`;
    const gameOpp = state.trainingGameByDate?.[iso];
    const isPast = iso < today;
    const isGameDay = !!gameOpp;
    const selectable = !isPast && !isGameDay;

    if (isGameDay) btn.classList.add("is-game");
    if (state.trainingSelectedDates.has(iso)) btn.classList.add("is-selected");

    const sessInfo = state.trainingSessionsByDate?.[iso];
    const sessType = sessInfo?.session?.type;
    const sessionLine = sessInfo ? trainingTypeLabel(sessType) : "";
    const risk = buildTrainingRiskFlags(iso);
    const badgeClass = sessInfo?.is_user_set ? "is-user" : "is-auto";
    const badgeLabel = sessInfo ? (sessInfo.is_user_set ? "수동" : "AUTO") : "";
    const riskCls = risk.level === "high" ? "is-high" : (risk.level === "medium" ? "is-medium" : "");
    const icon = trainingTypeIcon(sessType, isGameDay);

    btn.innerHTML = `
      <div class="training-day-head"><div class="training-day-date">${label}</div><span class="training-day-icon">${icon}</span></div>
      <div class="training-day-note">${gameOpp ? `vs ${gameOpp}` : ""}</div>
      <div class="training-day-sub">${!gameOpp ? sessionLine : "경기일"}</div>
      ${!gameOpp && sessInfo ? `<span class="training-session-badge ${badgeClass}">${badgeLabel}</span>` : ""}
      ${!isGameDay && !isPast ? `<span class="training-risk-dot ${riskCls}" title="${risk.reason}"></span>` : ""}
    `;

    if (!selectable) {
      btn.disabled = true;
    } else {
      btn.addEventListener("click", () => {
        if (state.trainingSelectedDates.has(iso)) state.trainingSelectedDates.delete(iso);
        else state.trainingSelectedDates.add(iso);
        renderTrainingCalendar();
        refreshTrainingTypeButtonsState();
        renderTrainingContextPanel();
      });
    }

    container.appendChild(btn);
  });
}

export { buildCalendar4Weeks, renderTrainingCalendar };
