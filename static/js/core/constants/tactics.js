const TACTICS_OFFENSE_SCHEMES = [
  { key: "Spread_HeavyPnR", label: "heavy_pnr" },
  { key: "Drive_Kick", label: "drive_kick" },
  { key: "FiveOut", label: "five_out" },
  { key: "Motion_SplitCut", label: "motion_split" },
  { key: "DHO_Chicago", label: "dho_chicago" },
  { key: "Post_InsideOut", label: "post_inside_out" },
  { key: "Horns_Elbow", label: "horns_elbow" },
  { key: "Transition_Early", label: "transition_early" }
];

const TACTICS_DEFENSE_SCHEMES = [
  { key: "Drop", label: "drop" },
  { key: "Switch_Everything", label: "switch_everything" },
  { key: "Switch_1_4", label: "switch_1_4" },
  { key: "Hedge_ShowRecover", label: "hedge_show_recover" },
  { key: "Blitz_TrapPnR", label: "blitz_trap" },
  { key: "AtTheLevel", label: "at_the_level" },
  { key: "Zone", label: "zone" }
];

const TACTICS_OFFENSE_ROLES = [
  "Engine_Primary", "Engine_Secondary", "Transition_Engine", "Shot_Creator", "Rim_Pressure",
  "SpotUp_Spacer", "Movement_Shooter", "Cutter_Finisher", "Connector",
  "Roll_Man", "ShortRoll_Hub", "Pop_Threat", "Post_Anchor"
];

const TACTICS_DEFENSE_ROLE_BY_SCHEME = {
  Drop: ["PnR_POA_Defender", "PnR_Cover_Big_Drop", "Lowman_Helper", "Nail_Helper", "Weakside_Rotator"],
  Switch_Everything: ["PnR_POA_Switch", "PnR_Cover_Big_Switch", "Switch_Wing_Strong", "Switch_Wing_Weak", "Backline_Anchor"],
  Switch_1_4: ["PnR_POA_Switch_1_4", "PnR_Cover_Big_Switch_1_4", "Switch_Wing_Strong_1_4", "Switch_Wing_Weak_1_4", "Backline_Anchor"],
  Hedge_ShowRecover: ["PnR_POA_Defender", "PnR_Cover_Big_HedgeRecover", "Lowman_Helper", "Nail_Helper", "Weakside_Rotator"],
  Blitz_TrapPnR: ["PnR_POA_Blitz", "PnR_Cover_Big_Blitz", "Lowman_Helper", "Nail_Helper", "Weakside_Rotator"],
  AtTheLevel: ["PnR_POA_AtTheLevel", "PnR_Cover_Big_AtTheLevel", "Lowman_Helper", "Nail_Helper", "Weakside_Rotator"],
  Zone: ["Zone_Top_Left", "Zone_Top_Right", "Zone_Bottom_Left", "Zone_Bottom_Right", "Zone_Bottom_Center"]
};

export { TACTICS_OFFENSE_SCHEMES, TACTICS_DEFENSE_SCHEMES, TACTICS_OFFENSE_ROLES, TACTICS_DEFENSE_ROLE_BY_SCHEME };
