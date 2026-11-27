/** Holding any of these lets a staff member see the approvals queue. */
export const APPROVER_PERMISSIONS = [
  "adjustment:approve:any",
  "wallet:unfreeze:any",
  "reversal:approve:any",
  "risk_rule:approve:any",
  "refund:approve:any",
  "config:approve:any",
  "role:assign:any",
];

/** Holding either lets a staff member see fee and limit configuration. */
export const CONFIG_PERMISSIONS = ["config:propose:any", "config:approve:any"];
