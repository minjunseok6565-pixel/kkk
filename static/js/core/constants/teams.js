import { state } from "../../app/state.js";

const TEAM_FULL_NAMES = {
  ATL: "애틀랜타 호크스", BOS: "보스턴 셀틱스", BKN: "브루클린 네츠", CHA: "샬럿 호네츠",
  CHI: "시카고 불스", CLE: "클리블랜드 캐벌리어스", DAL: "댈러스 매버릭스", DEN: "덴버 너기츠",
  DET: "디트로이트 피스톤스", GSW: "골든 스테이트 워리어스", HOU: "휴스턴 로키츠", IND: "인디애나 페이서스",
  LAC: "LA 클리퍼스", LAL: "LA 레이커스", MEM: "멤피스 그리즐리스", MIA: "마이애미 히트",
  MIL: "밀워키 벅스", MIN: "미네소타 팀버울브스", NOP: "뉴올리언스 펠리컨스", NYK: "뉴욕 닉스",
  OKC: "오클라호마시티 썬더", ORL: "올랜도 매직", PHI: "필라델피아 세븐티식서스", PHX: "피닉스 선즈",
  POR: "포틀랜드 트레일블레이저스", SAC: "새크라멘토 킹스", SAS: "샌안토니오 스퍼스", TOR: "토론토 랩터스",
  UTA: "유타 재즈", WAS: "워싱턴 위저즈"
};

const TEAM_LOGO_BASE_PATH = "/static/team_logos";

const TEAM_BRANDING = {
  ATL: { arenaName: "State Farm Arena", logoFile: "ATL.png" },
  BOS: { arenaName: "TD Garden", logoFile: "BOS.png" },
  BKN: { arenaName: "Barclays Center", logoFile: "BKN.png" },
  CHA: { arenaName: "Spectrum Center", logoFile: "CHA.png" },
  CHI: { arenaName: "United Center", logoFile: "CHI.png" },
  CLE: { arenaName: "Rocket Mortgage FieldHouse", logoFile: "CLE.png" },
  DAL: { arenaName: "American Airlines Center", logoFile: "DAL.png" },
  DEN: { arenaName: "Ball Arena", logoFile: "DEN.png" },
  DET: { arenaName: "Little Caesars Arena", logoFile: "DET.png" },
  GSW: { arenaName: "Chase Center", logoFile: "GSW.png" },
  HOU: { arenaName: "Toyota Center", logoFile: "HOU.png" },
  IND: { arenaName: "Gainbridge Fieldhouse", logoFile: "IND.png" },
  LAC: { arenaName: "Intuit Dome", logoFile: "LAC.png" },
  LAL: { arenaName: "Crypto.com Arena", logoFile: "LAL.png" },
  MEM: { arenaName: "FedExForum", logoFile: "MEM.png" },
  MIA: { arenaName: "Kaseya Center", logoFile: "MIA.png" },
  MIL: { arenaName: "Fiserv Forum", logoFile: "MIL.png" },
  MIN: { arenaName: "Target Center", logoFile: "MIN.png" },
  NOP: { arenaName: "Smoothie King Center", logoFile: "NOP.png" },
  NYK: { arenaName: "Madison Square Garden", logoFile: "NYK.png" },
  OKC: { arenaName: "Paycom Center", logoFile: "OKC.png" },
  ORL: { arenaName: "Kia Center", logoFile: "ORL.png" },
  PHI: { arenaName: "Wells Fargo Center", logoFile: "PHI.png" },
  PHX: { arenaName: "Footprint Center", logoFile: "PHX.png" },
  POR: { arenaName: "Moda Center", logoFile: "POR.png" },
  SAC: { arenaName: "Golden 1 Center", logoFile: "SAC.png" },
  SAS: { arenaName: "Frost Bank Center", logoFile: "SAS.png" },
  TOR: { arenaName: "Scotiabank Arena", logoFile: "TOR.png" },
  UTA: { arenaName: "Delta Center", logoFile: "UTA.png" },
  WAS: { arenaName: "Capital One Arena", logoFile: "WAS.png" },
};

function getTeamBranding(teamId) {
  const id = String(teamId || "").toUpperCase();
  const branding = TEAM_BRANDING[id] || { arenaName: "", logoFile: "" };
  const logoUrl = branding.logoFile ? `${TEAM_LOGO_BASE_PATH}/${branding.logoFile}` : "";
  return { ...branding, logoUrl };
}

function applyTeamLogo(el, teamId) {
  if (!el) return;
  const branding = getTeamBranding(teamId);
  if (branding.logoUrl) {
    el.style.backgroundImage = `url("${branding.logoUrl}")`;
    el.style.backgroundPosition = "center";
    el.style.backgroundRepeat = "no-repeat";
    el.style.backgroundSize = "contain";
    el.classList.add("team-logo-image");
    el.classList.add("has-team-logo");
    return;
  }
  el.style.backgroundImage = "";
  el.style.backgroundPosition = "";
  el.style.backgroundRepeat = "";
  el.style.backgroundSize = "";
  el.classList.remove("team-logo-image");
  el.classList.remove("has-team-logo");
}

function renderTeamLogoMark(teamId, extraClass = "") {
  const branding = getTeamBranding(teamId);
  const classes = ["team-logo-mark", extraClass, branding.logoUrl ? "has-image" : ""]
    .filter(Boolean)
    .join(" ");
  const style = branding.logoUrl ? ` style="background-image:url('${branding.logoUrl}')"` : "";
  return `<span class="${classes}" aria-hidden="true"${style}></span>`;
}

function getScheduleVenueText(game) {
  const label = String(game?.opponent_label || "").trim().toLowerCase();
  const isAwayGame = label.startsWith("@");
  const venueTeamId = isAwayGame
    ? String(game?.opponent_team_id || "").toUpperCase()
    : String(state.selectedTeamId || "").toUpperCase();
  return getTeamBranding(venueTeamId).arenaName || game?.opponent_team_name || game?.opponent_team_id || "";
}

export { TEAM_FULL_NAMES, TEAM_LOGO_BASE_PATH, TEAM_BRANDING, getTeamBranding, applyTeamLogo, renderTeamLogoMark, getScheduleVenueText };
