import { state } from "../../app/state.js";
import { TACTICS_OFFENSE_SCHEMES, TACTICS_DEFENSE_SCHEMES, TACTICS_OFFENSE_ROLES, TACTICS_DEFENSE_ROLE_BY_SCHEME } from "../../core/constants/tactics.js";

function tacticsSchemeLabel(schemes, key) {
  const found = (schemes || []).find((x) => x.key === key);
  return found ? found.label : key;
}

function tacticDisplayLabel(raw) {
  return String(raw || "-").replaceAll("_", " ");
}

function getDefenseRolesForScheme(key) {
  return TACTICS_DEFENSE_ROLE_BY_SCHEME[key] || TACTICS_DEFENSE_ROLE_BY_SCHEME.Drop;
}

function buildTacticsDraft(roster) {
  const names = (roster || []).map((r) => ({ id: String(r.player_id || ""), name: String(r.name || r.player_id || "-") })).filter((x) => x.id);
  const starters = [];
  const rotation = [];
  for (let i = 0; i < 5; i += 1) {
    const p = names[i];
    starters.push({
      pid: p?.id || "",
      offenseRole: TACTICS_OFFENSE_ROLES[i % TACTICS_OFFENSE_ROLES.length],
      defenseRole: getDefenseRolesForScheme("Drop")[i],
      minutes: 32 - i
    });
  }
  for (let i = 5; i < 10; i += 1) {
    const p = names[i];
    rotation.push({
      pid: p?.id || "",
      offenseRole: TACTICS_OFFENSE_ROLES[i % TACTICS_OFFENSE_ROLES.length],
      defenseRole: getDefenseRolesForScheme("Drop")[i - 5],
      minutes: 18 - (i - 5)
    });
  }
  return { offenseScheme: "Spread_HeavyPnR", defenseScheme: "Drop", starters, rotation, baselineHash: "" };
}

function rosterNameByPid(pid) {
  const row = (state.rosterRows || []).find((x) => String(x.player_id) === String(pid));
  return row ? String(row.name || row.player_id) : "-";
}

function computeTacticsInsights() {
  const allRows = [...state.tacticsDraft.starters, ...state.tacticsDraft.rotation];
  const starterMinutes = state.tacticsDraft.starters.reduce((sum, r) => sum + Math.max(0, Number(r.minutes || 0)), 0);
  const rotationMinutes = state.tacticsDraft.rotation.reduce((sum, r) => sum + Math.max(0, Number(r.minutes || 0)), 0);
  const totalMinutes = starterMinutes + rotationMinutes;
  const minutesDelta = 240 - totalMinutes;

  const offenseCount = new Map();
  const defenseCount = new Map();
  allRows.forEach((r) => {
    offenseCount.set(r.offenseRole, (offenseCount.get(r.offenseRole) || 0) + 1);
    defenseCount.set(r.defenseRole, (defenseCount.get(r.defenseRole) || 0) + 1);
  });

  const warnings = [];
  if (minutesDelta !== 0) {
    warnings.push({
      level: Math.abs(minutesDelta) >= 8 ? 'err' : 'warn',
      text: `총 출전시간이 ${Math.abs(minutesDelta)}분 ${minutesDelta > 0 ? '부족' : '초과'}했습니다.`
    });
  }
  const dupDef = [...defenseCount.entries()].filter(([, c]) => c > 1);
  if (dupDef.length) warnings.push({ level: 'warn', text: `수비 역할 중복 ${dupDef.length}개가 있습니다.` });
  const lowCreator = state.tacticsDraft.rotation.filter((r) => String(r.offenseRole || '').includes('Engine') || String(r.offenseRole || '').includes('Shot_Creator')).length;
  if (lowCreator === 0) warnings.push({ level: 'warn', text: '벤치 유닛에 볼 핸들러 역할이 부족합니다.' });

  return {
    allRows,
    totalMinutes,
    minutesDelta,
    starterAvg: starterMinutes / (state.tacticsDraft.starters.length || 1),
    rotationAvg: rotationMinutes / (state.tacticsDraft.rotation.length || 1),
    roleDiversity: offenseCount.size / (allRows.length || 1),
    offenseCount,
    defenseCount,
    warnings,
  };
}

function rowHealthState(row, insights) {
  const minute = Number(row.minutes || 0);
  const dCount = insights.defenseCount.get(row.defenseRole) || 0;
  if (minute < 8 || minute > 40) return { cls: 'warn', text: 'MIN' };
  if (dCount > 1) return { cls: 'err', text: 'DUP' };
  return { cls: 'ok', text: 'OK' };
}

export { tacticsSchemeLabel, tacticDisplayLabel, getDefenseRolesForScheme, buildTacticsDraft, rosterNameByPid, computeTacticsInsights, rowHealthState };
