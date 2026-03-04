import { state } from "../../app/state.js";
import { els } from "../../app/dom.js";
import { activateScreen } from "../../app/router.js";
import { fetchJson, setLoading } from "../../core/api.js";
import { num, clamp } from "../../core/guards.js";
import { formatPercent, formatSignedDelta } from "../../core/format.js";
import { renderEmptyScheduleRow } from "../schedule/scheduleScreen.js";

function renderMedicalEmpty(tbody, colSpan, text) {
  tbody.innerHTML = `<tr><td colspan="${colSpan}" class="schedule-empty">${text}</td></tr>`;
}

function riskTierClass(tier) {
  const t = String(tier || '').toUpperCase();
  if (t === 'HIGH') return 'status-danger';
  if (t === 'MEDIUM') return 'status-warn';
  return 'status-ok';
}

function renderMedicalHero(alerts = {}) {
  const p = alerts?.primary_alert_player;
  const load = alerts?.team_load_context || {};
  const level = String(alerts?.alert_level || 'info').toUpperCase();

  els.medicalAlertLevel.textContent = level;
  els.medicalAlertLevel.className = `medical-alert-badge ${level === 'CRITICAL' ? 'level-critical' : level === 'WARN' ? 'level-warn' : ''}`;

  if (!p) {
    els.medicalAlertText.textContent = '현재 주요 경고가 없습니다.';
    els.medicalAlertMeta.textContent = `다음 7일 경기 ${num(load?.next_7d_game_count, 0)}회 · B2B ${num(load?.next_7d_back_to_back_count, 0)}회`;
    return;
  }

  els.medicalAlertText.textContent = `${p.name || '-'} 리스크 ${p.risk_tier || '-'} (${num(p.risk_score, 0)})`;
  els.medicalAlertMeta.textContent = `${p.injury_status || '-'} · OUT ${p.out_until_date || '-'} / RETURNING ${p.returning_until_date || '-'} · 다음 7일 ${num(load?.next_7d_game_count, 0)}경기 (B2B ${num(load?.next_7d_back_to_back_count, 0)}회)`;
}

function renderMedicalTimeline(playerName, events) {
  els.medicalTimelineTitle.textContent = playerName ? `${playerName} 최근 부상 타임라인` : '워치리스트에서 선수를 선택하세요.';
  if (!events || !events.length) {
    els.medicalTimelineList.innerHTML = '<p class="empty-copy">최근 이벤트가 없습니다.</p>';
    return;
  }
  els.medicalTimelineList.innerHTML = events.map((e) => `
    <article class="medical-timeline-item">
      <p><strong>${e.date || '-'}</strong> · ${e.context || '-'}</p>
      <p>${e.body_part || '-'} / ${e.injury_type || '-'} / severity ${num(e.severity, 0)}</p>
      <p>OUT ~ ${e.out_until_date || '-'} · RETURNING ~ ${e.returning_until_date || '-'}</p>
    </article>
  `).join('');
}

function renderMedicalActionRecommendations(payload, playerName) {
  const items = payload?.recommendations || [];
  if (!items.length) {
    els.medicalActionList.innerHTML = '<p class="empty-copy">권고안이 없습니다.</p>';
    return;
  }
  els.medicalActionList.innerHTML = items.map((it) => {
    const d = it.expected_delta || {};
    const riskDelta = num(d.risk_score, 0);
    const stDelta = num(d.short_term_fatigue, 0);
    const ltDelta = num(d.long_term_fatigue, 0);
    const sharpDelta = num(d.sharpness, 0);
    return `
      <article class="medical-action-item">
        <strong>${it.label || it.action_id || '-'}</strong>
        <p>${playerName || '-'} 예상 변화 · Risk ${riskDelta > 0 ? '+' : ''}${riskDelta} · ST ${stDelta > 0 ? '+' : ''}${stDelta.toFixed(3)} · LT ${ltDelta > 0 ? '+' : ''}${ltDelta.toFixed(3)} · Sharp ${sharpDelta > 0 ? '+' : ''}${sharpDelta.toFixed(2)}</p>
      </article>
    `;
  }).join('');
}

function renderMedicalRiskCalendar(payload) {
  const days = payload?.days || [];
  if (!days.length) {
    els.medicalRiskCalendarList.innerHTML = '<p class="empty-copy">캘린더 데이터가 없습니다.</p>';
    return;
  }
  els.medicalRiskCalendarList.innerHTML = days.map((d) => `
    <article class="medical-day-card ${d.is_game_day ? 'is-game' : ''} ${d.is_back_to_back ? 'is-b2b' : ''}">
      <div class="date">${d.date || '-'}</div>
      <div class="meta">${d.is_game_day ? `vs/@ ${d.opponent_team_id || '-'}` : 'No Game'} · ${d.practice_session_type || '훈련 미정'}</div>
      <div class="badges">
        <span class="badge">HIGH ${num(d.high_risk_player_count, 0)}</span>
        <span class="badge">OUT ${num(d.out_player_count, 0)}</span>
        <span class="badge">RET ${num(d.returning_player_count, 0)}</span>
        <span class="badge">EVT ${num(d.injury_event_count, 0)}</span>
      </div>
    </article>
  `).join('');
}

async function loadMedicalPlayerContext(playerId, playerName) {
  if (!playerId || !state.selectedTeamId) return;
  setLoading(true, '선수 메디컬 컨텍스트를 불러오는 중...');
  try {
    const [timelinePayload, actionPayload] = await Promise.all([
      fetchJson(`/api/medical/team/${encodeURIComponent(state.selectedTeamId)}/players/${encodeURIComponent(playerId)}/timeline`),
      fetchJson(`/api/medical/team/${encodeURIComponent(state.selectedTeamId)}/players/${encodeURIComponent(playerId)}/action-recommendations`),
    ]);
    const resolvedName = playerName || timelinePayload?.player?.name || '-';
    renderMedicalTimeline(resolvedName, timelinePayload?.timeline?.events || []);
    renderMedicalActionRecommendations(actionPayload, resolvedName);
  } catch (e) {
    renderMedicalTimeline(playerName || '-', []);
    els.medicalActionList.innerHTML = `<p class="empty-copy">권고안 로딩 실패: ${e.message}</p>`;
  } finally {
    setLoading(false);
  }
}

function renderMedicalOverview(overview, alerts) {
  const summary = overview?.summary || {};
  const statusCounts = summary?.injury_status_counts || {};
  const riskCounts = summary?.risk_tier_counts || {};
  const watch = overview?.watchlists || {};
  const delta = alerts?.kpi_delta_7d || {};

  els.medicalAsOf.textContent = `기준일 ${overview?.as_of_date || '-'}`;
  els.medicalRosterCount.textContent = num(summary?.roster_count, 0);
  els.medicalOutCount.textContent = num(statusCounts?.OUT, 0);
  els.medicalReturningCount.textContent = `복귀 관리: ${num(statusCounts?.RETURNING, 0)}명`;
  els.medicalHighRiskCount.textContent = num(riskCounts?.HIGH, 0);
  els.medicalHealthFrustrationCount.textContent = num(summary?.health_frustration?.high_count, 0);

  const rosterDelta = formatSignedDelta(0);
  const outDelta = formatSignedDelta(delta?.out_count_delta);
  const hrDelta = formatSignedDelta(delta?.high_risk_count_delta);
  const healthDelta = formatSignedDelta(delta?.health_high_count_delta);
  els.medicalRosterDelta.textContent = rosterDelta.text;
  els.medicalOutDelta.textContent = outDelta.text;
  els.medicalOutDelta.className = `medical-delta ${outDelta.cls}`;
  els.medicalHighRiskDelta.textContent = hrDelta.text;
  els.medicalHighRiskDelta.className = `medical-delta ${hrDelta.cls}`;
  els.medicalHealthDelta.textContent = healthDelta.text;
  els.medicalHealthDelta.className = `medical-delta ${healthDelta.cls}`;

  const riskRows = watch?.highest_risk || [];
  if (!riskRows.length) {
    renderMedicalEmpty(els.medicalRiskBody, 6, '위험 데이터가 없습니다.');
  } else {
    els.medicalRiskBody.innerHTML = '';
    riskRows.forEach((r) => {
      const tr = document.createElement('tr');
      tr.className = 'roster-row';
      const riskScore = num(r.risk_score, 0);
      const reinjuryTotal = Object.values(r?.risk_inputs?.reinjury_count || {}).reduce((acc, v) => acc + num(v, 0), 0);
      tr.innerHTML = `
        <td>${r.name || '-'} <span class="schedule-opponent-name">${r.pos || '-'} · ${num(r.age, 0)}세</span></td>
        <td><span class="status-line ${riskTierClass(r.injury_status)}">${r.injury_status || '-'}</span></td>
        <td>
          <strong class="${riskTierClass(r.risk_tier)}">${r.risk_tier || '-'} (${riskScore})</strong>
          <div class="medical-risk-meter"><span style="width:${clamp(riskScore, 0, 100)}%"></span></div>
        </td>
        <td>${formatPercent(1 - num(r.condition?.short_term_fatigue, 0))} / ${formatPercent(1 - num(r.condition?.long_term_fatigue, 0))}</td>
        <td>${Math.round(num(r.condition?.sharpness, 0))}</td>
        <td>${reinjuryTotal}</td>
      `;
      tr.addEventListener('click', () => {
        state.medicalSelectedPlayerId = r.player_id;
        loadMedicalPlayerContext(r.player_id, r.name).catch((e) => alert(e.message));
      });
      els.medicalRiskBody.appendChild(tr);
    });
  }

  const injuredRows = watch?.currently_unavailable || [];
  els.medicalInjuredBody.innerHTML = injuredRows.length ? injuredRows.map((r) => `
    <tr>
      <td>${r.name || '-'} <span class="schedule-opponent-name">${r.pos || '-'}</span></td>
      <td><span class="status-line ${riskTierClass(r.recovery_status)}">${r.recovery_status || '-'}</span></td>
      <td>${r.injury_current?.body_part || '-'} (${r.injury_current?.injury_type || '-'})</td>
      <td>${r.injury_current?.out_until_date || '-'} ~ ${r.injury_current?.returning_until_date || '-'}</td>
    </tr>
  `).join('') : renderEmptyScheduleRow(4, '결장/복귀 관리 대상이 없습니다.');

  const healthRows = watch?.health_frustration_high || [];
  els.medicalHealthBody.innerHTML = healthRows.length ? healthRows.map((r) => `
    <tr>
      <td>${r.name || '-'} <span class="schedule-opponent-name">${r.pos || '-'}</span></td>
      <td>${num(r.health_frustration, 2)}</td>
      <td>${num(r.trade_request_level, 0)}</td>
      <td>${num(r.escalation_health, 0)}</td>
    </tr>
  `).join('') : renderEmptyScheduleRow(4, '건강 불만 상위 선수가 없습니다.');
}

async function showMedicalScreen() {
  if (!state.selectedTeamId) {
    alert('먼저 팀을 선택해주세요.');
    return;
  }
  setLoading(true, '메디컬 센터 데이터를 불러오는 중...');
  try {
    const [overview, alerts, calendar] = await Promise.all([
      fetchJson(`/api/medical/team/${encodeURIComponent(state.selectedTeamId)}/overview`),
      fetchJson(`/api/medical/team/${encodeURIComponent(state.selectedTeamId)}/alerts`).catch(() => ({})),
      fetchJson(`/api/medical/team/${encodeURIComponent(state.selectedTeamId)}/risk-calendar?days=14`).catch(() => ({ days: [] })),
    ]);
    state.medicalOverview = overview;
    const teamName = state.selectedTeamName || TEAM_FULL_NAMES[state.selectedTeamId] || state.selectedTeamId;
    els.medicalTitle.textContent = `${teamName} 메디컬 센터`;

    renderMedicalHero(alerts);
    renderMedicalOverview(overview, alerts);
    renderMedicalRiskCalendar(calendar);

    const primaryPlayerId = alerts?.primary_alert_player?.player_id;
    const primaryPlayerName = alerts?.primary_alert_player?.name;
    els.medicalAlertOpenPlayer.onclick = () => {
      if (!primaryPlayerId) return;
      state.medicalSelectedPlayerId = primaryPlayerId;
      loadMedicalPlayerContext(primaryPlayerId, primaryPlayerName).catch(() => {});
    };
    els.medicalAlertOpenAction.onclick = els.medicalAlertOpenPlayer.onclick;

    const first = primaryPlayerId ? { player_id: primaryPlayerId, name: primaryPlayerName } : (overview?.watchlists?.highest_risk || [])[0];
    if (first?.player_id) {
      state.medicalSelectedPlayerId = first.player_id;
      await loadMedicalPlayerContext(first.player_id, first.name);
    } else {
      renderMedicalTimeline(null, []);
      els.medicalActionList.innerHTML = '<p class="empty-copy">권고안이 없습니다.</p>';
    }

    activateScreen(els.medicalScreen);
  } finally {
    setLoading(false);
  }
}

export { renderMedicalEmpty, riskTierClass, renderMedicalHero, renderMedicalTimeline, renderMedicalActionRecommendations, renderMedicalRiskCalendar, loadMedicalPlayerContext, renderMedicalOverview, showMedicalScreen };
