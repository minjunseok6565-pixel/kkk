import { state } from "../../app/state.js";
import { els } from "../../app/dom.js";
import { fetchJson } from "../../core/api.js";
import { dateToIso, parseIsoDate, addDays } from "../../core/format.js";
import { TACTICS_OFFENSE_SCHEMES, TACTICS_DEFENSE_SCHEMES } from "../../core/constants/tactics.js";
import { loadTrainingData, prefetchTrainingCoreData } from "./trainingScreen.js";
import { CACHE_EVENT_TYPES, getPrefetchPlanForEvent, runPrefetchPlan } from "../../app/cachePolicy.js";
import { emitCacheEvent } from "../../app/cacheEvents.js";
import { renderTrainingCalendar } from "./trainingCalendar.js";

function trainingTypeLabel(t) {
  const m = {
    OFF_TACTICS: "공격",
    DEF_TACTICS: "수비",
    FILM: "필름",
    SCRIMMAGE: "청백전",
    RECOVERY: "휴식",
    REST: "없음"
  };
  return m[String(t || "").toUpperCase()] || "-";
}

function trainingTypeIcon(t, isGameDay) {
  if (isGameDay) return "🏟";
  const m = {
    OFF_TACTICS: "⚔",
    DEF_TACTICS: "🛡",
    FILM: "🎬",
    SCRIMMAGE: "🏀",
    RECOVERY: "🧊",
    REST: "⏸"
  };
  return m[String(t || "").toUpperCase()] || "•";
}

function buildTrainingDerivedMetrics() {
  const today = state.currentDate;
  const next7 = state.trainingCalendarDays.filter((d) => d >= today).slice(0, 7);
  const sessions = next7.map((d) => state.trainingSessionsByDate?.[d]?.session?.type).filter(Boolean);
  const gameCount = next7.filter((d) => !!state.trainingGameByDate?.[d]).length;
  const restCount = sessions.filter((t) => ["RECOVERY", "REST"].includes(String(t || "").toUpperCase())).length;
  const trainCount = sessions.length - restCount;
  const nextGame = next7.find((d) => !!state.trainingGameByDate?.[d]);
  const dDay = nextGame ? Math.max(0, Math.round((parseIsoDate(nextGame) - parseIsoDate(today)) / (1000 * 60 * 60 * 24))) : null;
  const offenseCount = sessions.filter((t) => String(t || "").toUpperCase() === "OFF_TACTICS").length;
  const offenseRatio = sessions.length ? offenseCount / sessions.length : 0;
  const hasBackToBack = next7.some((d) => state.trainingGameByDate?.[d] && state.trainingGameByDate?.[dateToIso(addDays(parseIsoDate(d), 1))]);
  return {
    rangeStart: state.trainingCalendarDays[0],
    rangeEnd: state.trainingCalendarDays[state.trainingCalendarDays.length - 1],
    trainCount,
    gameCount,
    restCount,
    nextGame,
    dDay,
    offenseRatio,
    hasBackToBack,
  };
}

function buildTrainingRiskFlags(iso) {
  const cur = parseIsoDate(iso);
  if (!cur) return { level: "low", reason: "" };
  const prevIso = dateToIso(addDays(cur, -1));
  const nextIso = dateToIso(addDays(cur, 1));
  const prevGame = !!state.trainingGameByDate?.[prevIso];
  const nextGame = !!state.trainingGameByDate?.[nextIso];
  if (prevGame && nextGame) return { level: "high", reason: "연전 사이 일정" };
  if (prevGame || nextGame) return { level: "medium", reason: "경기 인접 일정" };
  return { level: "low", reason: "일반 일정" };
}

function buildTrainingRecommendation(selectedDates, type = null) {
  if (!selectedDates.length) {
    return {
      title: "선택 대기",
      body: "날짜를 선택하면 일정 기반 추천 훈련이 표시됩니다.",
    };
  }

  const sorted = [...selectedDates].sort();
  const hasPreGame = sorted.some((iso) => !!state.trainingGameByDate?.[dateToIso(addDays(parseIsoDate(iso), 1))]);
  const hasPostGame = sorted.some((iso) => !!state.trainingGameByDate?.[dateToIso(addDays(parseIsoDate(iso), -1))]);
  const selectedType = String(type || "").toUpperCase();
  const metrics = buildTrainingDerivedMetrics();
  if (hasPreGame && ["OFF_TACTICS", "DEF_TACTICS", "SCRIMMAGE"].includes(selectedType)) {
    return {
      title: "경기 전날 고강도 경고",
      body: "내일 경기 일정이 있어 필름/회복 훈련이 더 안정적입니다.",
    };
  }
  if (metrics.gameCount >= 3 && metrics.restCount <= 1) {
    return {
      title: "회복 세션 보강 권장",
      body: "7일 내 경기 밀도가 높아 최소 1회 회복 세션을 확보하는 것이 좋습니다.",
    };
  }
  if (metrics.offenseRatio >= 0.6) {
    return {
      title: "훈련 편중 경고",
      body: "공격 전술 비중이 높습니다. 수비/필름 훈련으로 균형을 맞추세요.",
    };
  }
  if (hasPostGame) {
    return {
      title: "경기 다음날 회복 추천",
      body: "경기 다음날은 RECOVERY 배치 시 피로 누적 관리에 유리합니다.",
    };
  }
  return {
    title: "균형 상태 양호",
    body: "현재 일정 밀도 기준으로 선택한 훈련 구성이 무난합니다.",
  };
}

function renderTrainingSummaryStrip() {
  if (!els.trainingSummaryStrip) return;
  const m = buildTrainingDerivedMetrics();
  const range = m.rangeStart && m.rangeEnd
    ? `${String(m.rangeStart).slice(5)} ~ ${String(m.rangeEnd).slice(5)}`
    : "-";
  const nextOpp = m.nextGame ? state.trainingGameByDate?.[m.nextGame] : null;
  const dDay = m.dDay == null ? "-" : `D-${m.dDay}`;
  const risk = [];
  if (m.hasBackToBack) risk.push("연전 구간");
  if (m.restCount <= 1) risk.push("휴식 부족");
  if (m.offenseRatio >= 0.6) risk.push("공격 편중");
  const riskLabel = risk.length ? risk.join(" · ") : "안정";

  els.trainingSummaryStrip.innerHTML = `
    <article class="training-kpi-card">
      <p class="training-kpi-title">캘린더 범위</p>
      <p class="training-kpi-value">${range}</p>
      <p class="training-kpi-sub">4주 훈련 계획 구간</p>
    </article>
    <article class="training-kpi-card">
      <p class="training-kpi-title">이번 7일 요약</p>
      <p class="training-kpi-value">훈련 ${m.trainCount} · 경기 ${m.gameCount}</p>
      <p class="training-kpi-sub">휴식 ${m.restCount}일</p>
    </article>
    <article class="training-kpi-card">
      <p class="training-kpi-title">다음 경기</p>
      <p class="training-kpi-value">${nextOpp ? `vs ${nextOpp}` : "일정 없음"}</p>
      <p class="training-kpi-sub">${m.nextGame || "-"} · ${dDay}</p>
    </article>
    <article class="training-kpi-card">
      <p class="training-kpi-title">리스크 상태</p>
      <p class="training-kpi-value">${riskLabel}</p>
      <p class="training-kpi-sub">일정/편중도 기반</p>
    </article>
  `;
}

function renderTrainingContextPanel(type = null) {
  if (!els.trainingContextPanel) return;
  const selected = [...state.trainingSelectedDates].sort();
  const rec = buildTrainingRecommendation(selected, type || state.trainingActiveType);
  if (!selected.length) {
    els.trainingContextPanel.innerHTML = '<p class="empty-copy">캘린더에서 날짜를 선택하면 일정 맥락과 추천 훈련이 표시됩니다.</p>';
    return;
  }
  const first = selected[0];
  const last = selected[selected.length - 1];
  const firstRisk = buildTrainingRiskFlags(first);
  const prevIso = dateToIso(addDays(parseIsoDate(first), -1));
  const nextIso = dateToIso(addDays(parseIsoDate(last), 1));
  const prevGame = state.trainingGameByDate?.[prevIso];
  const nextGame = state.trainingGameByDate?.[nextIso];

  els.trainingContextPanel.innerHTML = `
    <h3 class="training-context-title">선택 일정 컨텍스트</h3>
    <ul class="training-context-kv">
      <li><span>선택 날짜</span><strong>${selected.length}일</strong></li>
      <li><span>구간</span><strong>${first} ~ ${last}</strong></li>
      <li><span>전날 경기</span><strong>${prevGame ? `vs ${prevGame}` : "없음"}</strong></li>
      <li><span>다음날 경기</span><strong>${nextGame ? `vs ${nextGame}` : "없음"}</strong></li>
      <li><span>대표 위험도</span><strong>${firstRisk.level.toUpperCase()} · ${firstRisk.reason}</strong></li>
    </ul>
    <div class="training-recommend">
      <strong>${rec.title}</strong>
      <p>${rec.body}</p>
    </div>
  `;
}

function refreshTrainingTypeButtonsState() {
  if (!els.trainingTypeButtons) return;
  const hasSelection = state.trainingSelectedDates.size > 0;
  els.trainingTypeButtons.querySelectorAll("button[data-training-type]").forEach((btn) => {
    btn.disabled = !hasSelection;
    btn.setAttribute("aria-disabled", hasSelection ? "false" : "true");
    btn.title = hasSelection ? "" : "날짜를 먼저 선택하세요.";
  });
}

function displaySchemeName(key) {
  return String(key || "-").replaceAll("_", " ");
}

function buildSchemeRows(schemeType) {
  const baseList = schemeType === "offense" ? TACTICS_OFFENSE_SCHEMES : TACTICS_DEFENSE_SCHEMES;
  const famList = schemeType === "offense" ? state.trainingFamiliarity.offense : state.trainingFamiliarity.defense;
  const famMap = new Map((famList || []).map((r) => [String(r.scheme_key), Number(r.value || 0)]));
  return (baseList || []).map((s) => ({
    key: s.key,
    value: famMap.has(s.key) ? famMap.get(s.key) : 0,
  }));
}

function trainingImpactLevelLabel(score) {
  if (score >= 2.5) return "매우 높음";
  if (score >= 1.2) return "높음";
  if (score > 0.1) return "보통";
  if (score >= -0.4) return "낮음";
  return "매우 낮음";
}

function trainingRhythmLabel(avgSharpnessDelta) {
  if (avgSharpnessDelta >= 1.2) return "실전 감각이 크게 올라갑니다.";
  if (avgSharpnessDelta >= 0.4) return "실전 감각 유지에 도움이 됩니다.";
  if (avgSharpnessDelta > -0.2) return "실전 감각 변화는 제한적입니다.";
  return "실전 감각이 떨어질 수 있어 경기 투입 전 점검이 필요합니다.";
}

function trainingLoadLabel(avgIntensity) {
  if (avgIntensity >= 1.12) return { label: "높음", tone: "is-caution" };
  if (avgIntensity >= 0.95) return { label: "보통", tone: "is-neutral" };
  return { label: "낮음", tone: "is-positive" };
}

function trainingImpactTone(levelLabel) {
  if (["매우 높음", "높음"].includes(levelLabel)) return "is-positive";
  if (levelLabel === "보통") return "is-neutral";
  return "is-caution";
}

function renderPreviewText(preview) {
  if (!preview) return '<p class="empty-copy">효과 프리뷰를 불러오지 못했습니다.</p>';
  const byPidRows = Object.values(preview.preview?.intensity_mult_by_pid || {});
  const avgSharpnessDelta = byPidRows.length
    ? (byPidRows.reduce((a, x) => a + Number(x.sharpness_delta || 0), 0) / byPidRows.length)
    : 0;
  const avgIntensity = byPidRows.length
    ? (byPidRows.reduce((a, x) => a + Number(x.intensity_mult || 1), 0) / byPidRows.length)
    : 1;

  const offenseGain = Number(preview.preview?.familiarity_gain?.offense_gain || 0);
  const defenseGain = Number(preview.preview?.familiarity_gain?.defense_gain || 0);
  const offenseLevel = trainingImpactLevelLabel(offenseGain);
  const defenseLevel = trainingImpactLevelLabel(defenseGain);
  const rhythmCopy = trainingRhythmLabel(avgSharpnessDelta);
  const load = trainingLoadLabel(avgIntensity);

  const dateIso = String(preview.date_iso || "").slice(0, 10);
  const risk = dateIso ? buildTrainingRiskFlags(dateIso) : { level: "low", reason: "일반 일정" };
  const riskKorean = risk.level === "high" ? "높음" : (risk.level === "medium" ? "주의" : "안정");

  const sessionType = String(preview.session?.type || "").toUpperCase();
  const participantCount = Array.isArray(preview.session?.participant_pids) ? preview.session.participant_pids.length : 0;
  const scopeCopy = sessionType === "SCRIMMAGE"
    ? `청백전 참여 선수 중심으로 강도가 높고, 비참여 선수는 ${trainingTypeLabel(preview.session?.non_participant_type)} 루틴을 따릅니다.`
    : "해당 날짜 로스터 전체에 동일한 훈련 컨셉이 적용됩니다.";

  const coachLine = (() => {
    if (risk.level === "high" && ["OFF_TACTICS", "DEF_TACTICS", "SCRIMMAGE"].includes(sessionType)) {
      return "경기 인접 일정입니다. 고강도 세션보다 필름/회복 중심 구성이 더 안전합니다.";
    }
    if (load.label === "높음") {
      return "훈련 완성도는 좋지만 누적 부담이 큽니다. 다음 일정에 회복 세션을 고려하세요.";
    }
    if (sessionType === "FILM") {
      return "전술 이해도와 경기 집중력을 안정적으로 끌어올리는 선택입니다.";
    }
    return "현재 일정에서는 균형 잡힌 선택입니다.";
  })();

  return `
    <div class="training-preview-report">
      <div class="training-preview-row">
        <p class="training-preview-head">기대 효과</p>
        <ul class="kv-list training-preview-list">
          <li><span>공격 조직력</span><strong class="${trainingImpactTone(offenseLevel)}">${offenseLevel}</strong></li>
          <li><span>수비 조직력</span><strong class="${trainingImpactTone(defenseLevel)}">${defenseLevel}</strong></li>
          <li><span>실전 감각</span><strong class="is-neutral">${rhythmCopy}</strong></li>
        </ul>
      </div>
      <div class="training-preview-row">
        <p class="training-preview-head">부담도</p>
        <ul class="kv-list training-preview-list">
          <li><span>훈련 강도 부담</span><strong class="${load.tone}">${load.label}</strong></li>
          <li><span>일정 리스크</span><strong class="${risk.level === "high" ? "is-caution" : "is-neutral"}">${riskKorean} · ${risk.reason}</strong></li>
        </ul>
      </div>
      <div class="training-preview-row">
        <p class="training-preview-head">적용 범위</p>
        <p class="training-preview-copy">${scopeCopy}</p>
        ${sessionType === "SCRIMMAGE" ? `<p class="training-preview-subcopy">청백전 참여 인원: ${participantCount}명</p>` : ""}
      </div>
      <div class="training-preview-row training-preview-coach">
        <p class="training-preview-head">코치 코멘트</p>
        <p class="training-preview-copy">${coachLine}</p>
      </div>
    </div>
  `;
}

async function renderTrainingDetail(type) {
  state.trainingActiveType = type;
  const selected = [...state.trainingSelectedDates].sort();
  if (!selected.length) {
    els.trainingDetailPanel.innerHTML = '<p class="empty-copy">적용할 날짜를 먼저 선택하세요.</p>';
    renderTrainingContextPanel(type);
    return;
  }

  const baseSession = {
    type,
    offense_scheme_key: null,
    defense_scheme_key: null,
    participant_pids: [],
    non_participant_type: "RECOVERY"
  };

  const offSchemeRows = buildSchemeRows("offense");
  const defSchemeRows = buildSchemeRows("defense");
  const offSchemes = offSchemeRows.map((x) => x.key);
  const defSchemes = defSchemeRows.map((x) => x.key);

  if (type === "OFF_TACTICS") baseSession.offense_scheme_key = offSchemes[0] || "Spread_HeavyPnR";
  if (type === "DEF_TACTICS") baseSession.defense_scheme_key = defSchemes[0] || "Drop";
  if (type === "FILM") {
    baseSession.offense_scheme_key = offSchemes[0] || "Spread_HeavyPnR";
    baseSession.defense_scheme_key = defSchemes[0] || "Drop";
  }
  if (type === "SCRIMMAGE") {
    baseSession.participant_pids = state.trainingRoster.slice(0, 10).map((r) => String(r.player_id));
    baseSession.non_participant_type = "RECOVERY";
  }

  state.trainingDraftSession = baseSession;

  const firstDate = selected[0];
  const preview = await fetchJson(`/api/practice/team/${encodeURIComponent(state.selectedTeamId)}/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ season_year: null, date_iso: firstDate, ...baseSession })
  }).catch(() => null);

  let extra = "";
  if (type === "OFF_TACTICS") {
    extra = `
      <div>
        <p class="training-section-title">공격 스킴 선택 (8개)</p>
        <div class="training-scheme-grid" id="training-off-scheme-grid">
          ${offSchemeRows.map((r) => `
            <button type="button" class="training-scheme-card ${r.key === baseSession.offense_scheme_key ? "is-selected" : ""}" data-off-scheme="${r.key}">
              <strong>${displaySchemeName(r.key)}</strong>
              <span>적응도 ${Math.round(r.value)}%</span>
            </button>
          `).join("")}
        </div>
      </div>
    `;
  } else if (type === "DEF_TACTICS") {
    extra = `
      <div>
        <p class="training-section-title">수비 스킴 선택 (7개)</p>
        <div class="training-scheme-grid" id="training-def-scheme-grid">
          ${defSchemeRows.map((r) => `
            <button type="button" class="training-scheme-card ${r.key === baseSession.defense_scheme_key ? "is-selected" : ""}" data-def-scheme="${r.key}">
              <strong>${displaySchemeName(r.key)}</strong>
              <span>적응도 ${Math.round(r.value)}%</span>
            </button>
          `).join("")}
        </div>
      </div>
    `;
  } else if (type === "SCRIMMAGE") {
    const selectedSet = new Set(baseSession.participant_pids);
    const rosterRows = state.trainingRoster.map((r) => `
      <tr>
        <td>${r.name || r.player_id}</td>
        <td>${Math.round(Number((r.short_term_stamina ?? 1) * 100))}%</td>
        <td>${Math.round(Number((r.long_term_stamina ?? 1) * 100))}%</td>
        <td>${Math.round(Number(r.sharpness ?? 50))}</td>
      </tr>
    `).join("");
    extra = `
      <p class="training-section-title">5대5 라인업 선택 (기본 10명)</p>
      <div class="training-player-select-grid" id="training-scrimmage-player-grid">
        ${state.trainingRoster.map((r) => {
          const pid = String(r.player_id);
          return `<button type="button" class="training-player-chip ${selectedSet.has(pid) ? "is-selected" : ""}" data-scrimmage-pid="${pid}">${r.name || pid}</button>`;
        }).join("")}
      </div>
      <p class="training-selected-copy">선택 선수: <strong id="training-scrimmage-selected-count">${baseSession.participant_pids.length}</strong>명</p>
      <table class="training-player-table">
        <thead><tr><th>선수</th><th>단기 체력</th><th>장기 체력</th><th>샤프니스</th></tr></thead>
        <tbody>${rosterRows}</tbody>
      </table>
    `;
  }

  els.trainingDetailPanel.innerHTML = `
    <div class="training-detail-grid">
      <h3>${trainingTypeLabel(type)} 훈련 설정</h3>
      <p>선택 날짜: ${selected.join(", ")}</p>
      ${extra}
      <div class="training-preview-box"><strong>연습 효과 프리뷰</strong><div id="training-preview-body">${renderPreviewText(preview)}</div></div>
      <div class="training-inline-row"><button id="training-apply-btn" class="btn btn-primary" type="button">선택 날짜에 적용</button></div>
    </div>
  `;
  renderTrainingContextPanel(type);

  async function refreshTrainingPreview() {
    const currentPreview = await fetchJson(`/api/practice/team/${encodeURIComponent(state.selectedTeamId)}/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ season_year: null, date_iso: firstDate, ...state.trainingDraftSession })
    }).catch(() => null);
    const previewBody = document.getElementById("training-preview-body");
    if (previewBody) previewBody.innerHTML = renderPreviewText(currentPreview);
  }

  document.querySelectorAll("[data-off-scheme]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      state.trainingDraftSession.offense_scheme_key = btn.dataset.offScheme;
      document.querySelectorAll("[data-off-scheme]").forEach((el) => el.classList.toggle("is-selected", el === btn));
      await refreshTrainingPreview();
    });
  });

  document.querySelectorAll("[data-def-scheme]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      state.trainingDraftSession.defense_scheme_key = btn.dataset.defScheme;
      document.querySelectorAll("[data-def-scheme]").forEach((el) => el.classList.toggle("is-selected", el === btn));
      await refreshTrainingPreview();
    });
  });

  document.querySelectorAll("[data-scrimmage-pid]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const pid = btn.dataset.scrimmagePid;
      const set = new Set(state.trainingDraftSession.participant_pids || []);
      if (set.has(pid)) set.delete(pid);
      else set.add(pid);
      state.trainingDraftSession.participant_pids = [...set];
      btn.classList.toggle("is-selected", set.has(pid));
      const count = document.getElementById("training-scrimmage-selected-count");
      if (count) count.textContent = String(state.trainingDraftSession.participant_pids.length);
      await refreshTrainingPreview();
    });
  });

  const applyBtn = document.getElementById("training-apply-btn");
  applyBtn.addEventListener("click", async () => {
    const dates = [...state.trainingSelectedDates];
    await Promise.all(dates.map((dateIso) => fetchJson("/api/practice/team/session/set", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        team_id: state.selectedTeamId,
        date_iso: dateIso,
        ...state.trainingDraftSession
      })
    })));
    const sorted = [...dates].sort();
    const trainingRange = {
      from: sorted[0] || state.currentDate,
      to: sorted[sorted.length - 1] || state.currentDate,
    };
    emitCacheEvent(CACHE_EVENT_TYPES.TRAINING_SAVE, { teamId: state.selectedTeamId, trainingRange });
    const prefetchPlan = getPrefetchPlanForEvent(CACHE_EVENT_TYPES.TRAINING_SAVE, { teamId: state.selectedTeamId, trainingRange });
    void runPrefetchPlan(prefetchPlan);
    await prefetchTrainingCoreData({ teamId: state.selectedTeamId, currentDate: state.currentDate, progressiveSessionHydration: true });
    await loadTrainingData();
    renderTrainingCalendar();
    alert(`${dates.length}일에 훈련을 적용했습니다.`);
  });
}

export { trainingTypeLabel, trainingTypeIcon, buildTrainingDerivedMetrics, buildTrainingRiskFlags, buildTrainingRecommendation, renderTrainingSummaryStrip, renderTrainingContextPanel, refreshTrainingTypeButtonsState, displaySchemeName, buildSchemeRows, trainingImpactLevelLabel, trainingRhythmLabel, trainingLoadLabel, trainingImpactTone, renderPreviewText, renderTrainingDetail };
