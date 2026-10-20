# ruff: noqa: E501 - an embedded HTML/JS asset, not Python code
"""The one-click control page: Chinese by default, English on request. No external assets."""

PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SpareAgent · 一键提供算力</title>
<style>
:root{--bg:#f6f5f1;--card:#fff;--ink:#1d1c1a;--muted:#6b6862;--line:#e4e1da;--accent:#1f7a4d;
--accent-ink:#fff;--warn:#9a5b00;--warn-bg:#fff6e5;--err:#b3261e;--err-bg:#fdecea;--off:#8c877e}
@media (prefers-color-scheme:dark){:root{--bg:#141412;--card:#1e1d1b;--ink:#f1efe9;--muted:#a19d95;
--line:#34322e;--accent:#3fb37a;--accent-ink:#08140e;--warn:#f0b35a;--warn-bg:#2b2416;--err:#ff8a80;
--err-bg:#2f1a18;--off:#6f6b64}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,
"PingFang SC","Hiragino Sans GB","Segoe UI",sans-serif}
main{max-width:760px;margin:0 auto;padding:28px 16px 60px}
header{display:flex;justify-content:space-between;align-items:center;gap:12px}
h1{font-size:20px;margin:0}h2{font-size:16px;margin:0 0 10px}
.muted{color:var(--muted)}small{color:var(--muted)}
.lang{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;
padding:4px 12px;cursor:pointer}
.hero{display:flex;flex-direction:column;align-items:center;text-align:center;padding:28px 0 8px}
.big{width:200px;height:200px;border-radius:50%;border:0;cursor:pointer;font-size:22px;font-weight:600;
background:var(--off);color:#fff;box-shadow:0 10px 30px rgba(0,0,0,.18);transition:transform .15s,background .2s}
.big:hover{transform:scale(1.03)}.big:disabled{cursor:progress;opacity:.85}
.big.online{background:var(--accent);color:var(--accent-ink)}
.big.busy{background:linear-gradient(135deg,var(--off),var(--accent));animation:pulse 1.6s infinite}
@keyframes pulse{50%{box-shadow:0 0 0 14px rgba(63,179,122,.15)}}
.big small{display:block;color:inherit;opacity:.85;font-size:13px;font-weight:400;margin-top:6px}
.state{margin-top:14px;font-size:15px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin-top:16px}
.consent ul{margin:8px 0 0;padding-left:20px}.consent li{margin:4px 0}
.row{display:flex;gap:12px;flex-wrap:wrap}.row>label{flex:1 1 160px}
label{display:block;font-size:13px;color:var(--muted)}
input,select{width:100%;margin-top:4px;padding:8px 10px;border:1px solid var(--line);border-radius:8px;
background:var(--bg);color:var(--ink);font-size:15px}
input[type=checkbox]{width:auto;margin-right:6px}
button.s{border:1px solid var(--line);background:var(--bg);color:var(--ink);border-radius:8px;
padding:7px 14px;cursor:pointer;font-size:14px}
button.p{background:var(--accent);color:var(--accent-ink);border-color:transparent}
.code{font:600 28px/1.2 ui-monospace,Menlo,monospace;letter-spacing:2px;margin:6px 0 10px;word-break:break-all}
.link{font-size:13px;word-break:break-all}
.notice{background:var(--warn-bg);color:var(--warn);border-radius:10px;padding:10px 12px;margin-top:12px;font-size:14px}
.error{background:var(--err-bg);color:var(--err);border-radius:10px;padding:10px 12px;margin-top:12px;font-size:14px}
table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:6px 4px;border-top:1px solid var(--line);text-align:left}
th{color:var(--muted);font-weight:500;border-top:0}
code{font-family:ui-monospace,Menlo,monospace;font-size:13px}
.steps{list-style:none;padding:0;margin:10px 0 0;text-align:left;display:inline-block}
.steps li{margin:3px 0;color:var(--muted)}.steps li.now{color:var(--ink);font-weight:600}
.steps li.done{color:var(--accent)}
.hidden{display:none}
</style>
</head>
<body>
<main>
<header>
  <div><h1 data-t="title"></h1><small data-t="subtitle"></small></div>
  <button class="lang" id="lang" type="button"></button>
</header>

<section class="hero">
  <button class="big" id="big" type="button"></button>
  <div class="state" id="state" role="status" aria-live="polite"></div>
  <ol class="steps hidden" id="steps"></ol>
  <button class="s hidden" id="cancel" type="button" data-t="cancel"></button>
  <div class="error hidden" id="error" role="alert"></div>
</section>

<section class="card consent" id="consent">
  <h2 data-t="before_title"></h2>
  <div class="row">
    <label><span data-t="device_name"></span><input id="device" maxlength="80"></label>
    <label><span data-t="plan"></span>
      <select id="plan"><option value="PLUS">ChatGPT Plus</option><option value="PRO">ChatGPT Pro</option></select>
    </label>
  </div>
  <p class="muted" data-t="consent_intro"></p>
  <ul id="ack"></ul>
</section>

<section class="card hidden" id="invite">
  <h2 data-t="invite_title"></h2>
  <div class="code" id="code"></div>
  <div class="link muted" id="link"></div>
  <div class="row" style="margin-top:10px">
    <button class="s p" id="copy" type="button" data-t="copy"></button>
    <button class="s" id="rotate" type="button" data-t="rotate"></button>
  </div>
  <p class="muted" data-t="invite_help"></p>
</section>

<section class="card hidden" id="settings">
  <h2 data-t="limits_title"></h2>
  <div class="row">
    <label><span data-t="max_members"></span><input id="max_members" type="number" min="2"></label>
    <label><span data-t="max_tasks"></span><input id="max_tasks" type="number" min="1" max="50"></label>
    <label><span data-t="max_minutes"></span><input id="max_minutes" type="number" min="1" max="60"></label>
  </div>
  <label style="margin-top:10px"><input id="auto" type="checkbox"><span data-t="auto_approve"></span></label>
  <div class="row" style="margin-top:10px;align-items:center">
    <button class="s p" id="save" type="button" data-t="save"></button>
    <small id="usage" role="status" aria-live="polite"></small>
  </div>
</section>

<section class="card hidden" id="members">
  <h2 data-t="members_title"></h2>
  <table><thead><tr><th data-t="member"></th><th data-t="joined"></th><th></th></tr></thead>
  <tbody id="member_rows"></tbody></table>
</section>

<section class="card hidden" id="tasks">
  <h2 data-t="tasks_title"></h2>
  <table><thead><tr><th data-t="time"></th><th data-t="kind"></th><th data-t="status"></th><th data-t="approval"></th></tr></thead>
  <tbody id="task_rows"></tbody></table>
  <p><button class="s" id="dashboard" type="button" data-t="open_dashboard"></button></p>
</section>

<div class="notice hidden" id="notice" role="status" aria-live="polite"></div>
<p><small data-t="footer"></small></p>
</main>
<script>
"use strict";
const T = {
zh: {
 title:"SpareAgent · 一键提供算力", subtitle:"把你空闲的 ChatGPT Codex 额度分享给自己的小组",
 lang:"English", cancel:"取消",
 go_online:"上线", go_online_sub:"开始提供算力", online:"在线中", online_sub:"点击下线",
 starting:"正在上线…", stopping:"正在下线…", retry:"重试上线",
 st_OFFLINE:"已下线：不会接任何任务。", st_ONLINE:"在线：小组成员的任务会自动批准并在这台电脑上运行。",
 st_STARTING:"正在上线，请按提示操作。", st_STOPPING:"正在下线…", st_ERROR:"上线失败：",
 step_codex_install:"检查 / 安装 Codex CLI（首次需下载约 100 MB）", step_git_check:"检查 Apple 命令行工具（代码任务需要）", step_codex_login:"登录你自己的 ChatGPT（浏览器会自动打开）",
 step_signup:"注册这台设备并创建你的小组", step_owner_check:"本机自检（用你自己的额度跑一句很短的提示）",
 step_server_online:"开启分享与自动批准", step_worker_start:"启动接单程序",
 step_server_offline:"暂停分享", step_worker_stop:"停止接单程序",
 before_title:"上线前请确认", device_name:"设备名称（小组成员会看到）", plan:"你的订阅",
 consent_intro:"点击「上线」即表示你本人确认以下全部内容（随时可以点一下下线）：",
 ack_1:"这是私人、非商业的实验，没有任何付费、转售或报酬。",
 ack_2:"我的 ChatGPT 账号只在这台电脑的官方 Codex CLI 里登录，密码和登录凭据不会交给 SpareAgent 或任何人。",
 ack_3:"小组成员提交的任务会在设置的上限内自动批准，并使用我自己的订阅额度在这台电脑上运行（代码审查，只读沙箱）。",
 ack_4:"我的账号没有额外购买或赠送的 Credits，并已关闭自动充值；分享只消耗订阅自带额度。",
 ack_5:"上线时会用我的额度运行一次很短的自检提示。",
 ack_6:"我可以随时调整人数和任务上限、移除成员、关闭自动批准或下线。",
 ack_7:"服务器运营者设置的总上限（例如小组最多人数）仍然适用。",
 invite_title:"小组邀请码", copy:"复制邀请链接", copied:"已复制", rotate:"换一个邀请码",
 invite_help:"把邀请链接或邀请码私下发给你信任的人。他们打开链接、勾选同意后即可加入并提交任务。换码后旧码立即失效。",
 invite_lost:"邀请码只在生成时显示一次。点「换一个邀请码」生成新的。",
 limits_title:"小组与任务上限（随时可改）", max_members:"小组人数上限（含你自己）",
 max_tasks:"每 24 小时最多任务数", max_minutes:"单个任务最长（分钟）",
 auto_approve:"自动批准小组成员的任务", save:"保存", saved:"已保存",
 usage:(n,m)=>`过去 24 小时已接 ${n} / ${m} 个任务`, cap:(c)=>`（服务器上限 ${c} 人）`,
 members_title:"小组成员", member:"成员", joined:"加入时间", you:"你", remove:"移除",
 confirm_remove:"移除该成员？其未完成的任务会被取消，邀请码也会更换。",
 tasks_title:"最近的任务", time:"时间", kind:"类型", status:"状态", approval:"批准",
 approved_auto:"自动批准", approved_owner:"你批准", pending:"等待批准", rejected:"已拒绝",
 open_dashboard:"打开网页控制台",
 live_blocked:"注意：服务器暂未开启真实执行，任务会排队等待，直到运营者开启。",
 footer:"关闭这个页面不会下线；关闭运行它的终端窗口或按 Ctrl-C 会自动下线。",
 e_bundle_missing:"缺少运营者提供的连接配置（bundle.json）。请用运营者给你的安装包重新安装。",
 e_codex_missing:"没有找到 Codex CLI，也没有 npm 或 Homebrew 可以自动安装。请先安装 Node.js（nodejs.org）后重试。",
 e_codex_install_failed:"自动安装 Codex CLI 失败，请检查网络后点「重试上线」。",
 e_git_missing:"需要安装 Apple 命令行工具：请在弹出的窗口点「安装」，完成后（约 5–15 分钟）点「重试上线」。", e_codex_login_failed:"Codex 登录没有完成。",
 e_codex_login_required:"需要用你自己的 ChatGPT 账号登录 Codex（不要用 API Key）。",
 e_owner_check_failed:"本机自检没有通过。请确认 Codex 能正常使用后重试。",
 e_server_rejected:"服务器拒绝了请求：", e_server_unreachable:"连不上服务器，请检查网络：",
 e_worker_stopped:"接单程序意外停止。", e_not_registered:"请先上线一次。",
 e_plan_invalid:"请选择订阅类型。", e_invalid_input:"输入有误：", e_unexpected:"发生意外错误：",
 s_QUEUED:"排队中", s_RESERVED:"已分配", s_RUNNING:"运行中", s_VERIFYING:"校验中", s_SUCCEEDED:"完成",
 s_FAILED:"失败", s_CANCELLED:"已取消", s_CANCELLING:"取消中", s_TIMED_OUT:"超时", s_CREATED:"已创建",
 s_AWAITING_APPROVAL:"等待批准", s_REJECTED:"已拒绝",
 tt_CODE_REVIEW:"代码审查", tt_TRANSLATE:"翻译", tt_DATA_ANALYSIS:"表格分析", tt_CODE_CHANGE:"修改代码",
 tt_WRITING:"写作与润色", tt_SUMMARIZE:"总结", tt_DOCUMENT_QA:"文档问答",
 m_LIVE:"真实执行", m_SIMULATED:"模拟",
 save_failed:"未保存：", saving:"正在保存…",
 r_members:(a,b)=>`小组人数需在 ${a}–${b} 之间。`, r_tasks:"每 24 小时任务数需在 1–50 之间。",
 r_minutes:"单个任务时长需在 1–60 分钟之间。", r_integer:"请填写整数。",
 sc_code:(c)=>`（错误代码 ${c}）`,
 sc_MEMBER_LIMIT_OUT_OF_RANGE:"小组人数超出允许范围：不能少于当前成员数，也不能超过服务器上限。",
 sc_GROUP_FULL:"小组已满，请先调高人数上限。", sc_GROUP_DISABLED:"这个小组已被停用，请联系运营者。",
 sc_SELF_SERVE_NOT_AUTHORIZED:"运营者的分享授权已撤销或过期，暂时不能上线。请联系运营者。",
 sc_AUTHORIZATION_REVOKED:"运营者已撤销分享授权，暂时不能运行任务。", sc_LIVE_NOT_AUTHORIZED:"运营者尚未授权真实执行。",
 sc_LIVE_EXECUTION_DISABLED:"服务器暂未开启真实执行。", sc_EXPERIMENT_DISABLED:"服务器暂未开放分享功能。",
 sc_SIGNUP_CODE_INVALID:"安装包里的注册码无效，请向运营者索取新的安装包。",
 sc_TERMS_VERSION_MISMATCH:"条款已更新，请重新打开本页面后再试。", sc_INVITE_CODE_INVALID:"邀请码无效。",
 sc_CANNOT_REMOVE_OWNER:"不能移除你自己。", sc_MEMBER_NOT_FOUND:"找不到该成员，可能已被移除。",
 sc_NOT_SELF_SERVE_NODE:"这台设备不是通过一键分享注册的。", sc_SELF_SERVE_GROUP_NOT_FOUND:"找不到你的小组，请重新上线。",
 sc_SUBSCRIPTION_AUTH_INVALID:"Codex 登录无效，请重新登录你自己的 ChatGPT 账号。",
 sc_SHARING_REVOKED:"分享已被撤销，请联系运营者。", sc_VALIDATION_ERROR:"提交的数值不在允许范围内。",
 sc_UNAUTHENTICATED:"设备凭据已失效，请重新上线。", sc_RATE_LIMITED:"操作太频繁，请稍后再试。"
},
en: {
 title:"SpareAgent · One-click sharing", subtitle:"Share your spare ChatGPT Codex capacity with your own group",
 lang:"中文", cancel:"Cancel",
 go_online:"Go online", go_online_sub:"Start sharing", online:"Online", online_sub:"Click to go offline",
 starting:"Going online…", stopping:"Going offline…", retry:"Retry",
 st_OFFLINE:"Offline: no tasks will run.", st_ONLINE:"Online: your group's tasks are auto-approved and run on this computer.",
 st_STARTING:"Going online — follow any prompts.", st_STOPPING:"Going offline…", st_ERROR:"Could not go online: ",
 step_codex_install:"Check / install the Codex CLI (first time: ~100 MB download)", step_git_check:"Check Apple command line tools (needed for code tasks)", step_codex_login:"Sign in with your own ChatGPT (a browser opens)",
 step_signup:"Register this device and create your group", step_owner_check:"Self-check (one tiny prompt on your own plan)",
 step_server_online:"Enable sharing and automatic approval", step_worker_start:"Start the worker",
 step_server_offline:"Pause sharing", step_worker_stop:"Stop the worker",
 before_title:"Before you go online", device_name:"Device name (visible to your group)", plan:"Your plan",
 consent_intro:"By clicking “Go online” you personally confirm all of the following (one more click goes offline any time):",
 ack_1:"This is a private, non-commercial experiment with no payment, resale or compensation.",
 ack_2:"My ChatGPT account is signed in only in the official Codex CLI on this computer; my credentials are never given to SpareAgent or anyone.",
 ack_3:"Tasks from my group's members are approved automatically within my limits and run on this computer with my own plan usage (code review, read-only sandbox).",
 ack_4:"My account holds no additional purchased or granted Credits and auto-reload is off; sharing only uses included plan usage.",
 ack_5:"Going online runs one very short self-check prompt on my plan.",
 ack_6:"I can change member and task limits, remove members, switch off automatic approval or go offline at any time.",
 ack_7:"The server operator's overall caps (such as the maximum group size) still apply.",
 invite_title:"Group invite code", copy:"Copy invite link", copied:"Copied", rotate:"New invite code",
 invite_help:"Send the link or code privately to people you trust. They open it, accept the terms and can submit tasks. A new code immediately invalidates the old one.",
 invite_lost:"The code is shown only when it is created. Click “New invite code” to issue another.",
 limits_title:"Group and task limits (change any time)", max_members:"Maximum members (including you)",
 max_tasks:"Maximum tasks per 24 hours", max_minutes:"Maximum minutes per task",
 auto_approve:"Approve my group's tasks automatically", save:"Save", saved:"Saved",
 usage:(n,m)=>`${n} / ${m} tasks in the last 24 hours`, cap:(c)=>` (server cap ${c})`,
 members_title:"Members", member:"Member", joined:"Joined", you:"You", remove:"Remove",
 confirm_remove:"Remove this member? Their unfinished tasks are cancelled and the invite code changes.",
 tasks_title:"Recent tasks", time:"Time", kind:"Type", status:"Status", approval:"Approval",
 approved_auto:"Automatic", approved_owner:"By you", pending:"Pending", rejected:"Rejected",
 open_dashboard:"Open web dashboard",
 live_blocked:"Note: real execution is not enabled on the server yet; tasks wait until the operator enables it.",
 footer:"Closing this page does not go offline; closing the Terminal window that runs it, or Ctrl-C, does.",
 e_bundle_missing:"The operator's connection file (bundle.json) is missing. Reinstall from the operator's package.",
 e_codex_missing:"Codex CLI not found and neither npm nor Homebrew is available to install it. Install Node.js (nodejs.org) and retry.",
 e_codex_install_failed:"Installing the Codex CLI failed; check the network and retry.",
 e_git_missing:"Apple's command line tools are needed: click Install in the dialog, then Retry when it finishes (about 5–15 minutes).", e_codex_login_failed:"Codex sign-in did not finish.",
 e_codex_login_required:"Sign in to Codex with your own ChatGPT account (not an API key).",
 e_owner_check_failed:"The self-check failed. Make sure Codex works, then retry.",
 e_server_rejected:"The server rejected the request: ", e_server_unreachable:"Cannot reach the server; check the network: ",
 e_worker_stopped:"The worker stopped unexpectedly.", e_not_registered:"Go online once first.",
 e_plan_invalid:"Choose your plan.", e_invalid_input:"Invalid input: ", e_unexpected:"Unexpected error: ",
 s_QUEUED:"Queued", s_RESERVED:"Assigned", s_RUNNING:"Running", s_VERIFYING:"Verifying", s_SUCCEEDED:"Done",
 s_FAILED:"Failed", s_CANCELLED:"Cancelled", s_CANCELLING:"Cancelling", s_TIMED_OUT:"Timed out", s_CREATED:"Created",
 s_AWAITING_APPROVAL:"Awaiting approval", s_REJECTED:"Rejected",
 tt_CODE_REVIEW:"Code review", tt_TRANSLATE:"Translation", tt_DATA_ANALYSIS:"Spreadsheet analysis", tt_CODE_CHANGE:"Code change",
 tt_WRITING:"Writing", tt_SUMMARIZE:"Summary", tt_DOCUMENT_QA:"Document Q&A",
 m_LIVE:"Live", m_SIMULATED:"Simulated",
 save_failed:"Not saved: ", saving:"Saving…",
 r_members:(a,b)=>`Members must be between ${a} and ${b}.`, r_tasks:"Tasks per 24 hours must be between 1 and 50.",
 r_minutes:"Minutes per task must be between 1 and 60.", r_integer:"Enter whole numbers.",
 sc_code:(c)=>` (error code ${c})`
}};
const ONLINE_STEPS=["codex_install","git_check","codex_login","signup","owner_check","server_online","worker_start"];
const OFFLINE_STEPS=["server_offline","worker_stop"];
const KEY=new URLSearchParams(location.hash.slice(1)).get("k")||"";
let lang=(()=>{try{return localStorage.getItem("sa_lang")}catch(e){return null}})()||"zh";
let S=null, editing=false, flash="", localErr=null;
const $=id=>document.getElementById(id);
const t=(k,...a)=>{const v=(T[lang]||T.zh)[k];return typeof v==="function"?v(...a):(v??k)};

async function call(route, body){
  const r=await fetch("/api/"+route,{method:"POST",headers:{"Content-Type":"application/json","X-App-Key":KEY},
    body:JSON.stringify(body||{})});
  const data=await r.json().catch(()=>({}));
  if(!r.ok){const e=data.error||{};throw Object.assign(new Error(e.detail||""),{code:e.code||"unexpected",server_code:e.server_code})}
  return data;
}
function has(k){return Object.prototype.hasOwnProperty.call(T[lang]||T.zh,k)}
function errText(err){
  if(err.code==="server_rejected"&&err.server_code){
    // Translated, actionable text for known codes; the raw server message only in English.
    if(has("sc_"+err.server_code))return t("sc_"+err.server_code);
    return t("e_server_rejected")+(lang==="en"&&err.detail?err.detail:"")+t("sc_code",err.server_code);
  }
  return t("e_"+err.code)+(err.detail?(" "+err.detail):"");
}
function label(p,v){return has(p+v)?t(p+v):String(v||"").replaceAll("_"," ").toLowerCase()}
function show(el,on){el.classList.toggle("hidden",!on)}
function fmt(ts){return ts?new Date(ts).toLocaleString(lang==="zh"?"zh-CN":"en-US",{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}):"—"}

function applyStatic(){
  document.documentElement.lang=lang==="zh"?"zh-CN":"en";
  document.title=t("title");
  document.querySelectorAll("[data-t]").forEach(el=>{el.textContent=t(el.dataset.t)});
  $("lang").textContent=t("lang");
  $("ack").innerHTML="";
  for(let i=1;i<=7;i++){const li=document.createElement("li");li.textContent=t("ack_"+i);$("ack").appendChild(li)}
}
function render(){
  if(!S){if(localErr){show($("error"),true);$("error").textContent=errText(localErr)}return}
  const big=$("big"), ph=S.phase;
  big.className="big"+(ph==="ONLINE"?" online":"")+(ph==="STARTING"||ph==="STOPPING"?" busy":"");
  big.disabled=ph==="STARTING"||ph==="STOPPING"||!KEY;
  big.innerHTML="";
  const main=ph==="ONLINE"?t("online"):ph==="STARTING"?t("starting"):ph==="STOPPING"?t("stopping"):ph==="ERROR"?t("retry"):t("go_online");
  const sub=ph==="ONLINE"?t("online_sub"):ph==="OFFLINE"?t("go_online_sub"):"";
  big.append(main); if(sub){const s=document.createElement("small");s.textContent=sub;big.append(s)}
  $("state").textContent=t("st_"+ph);
  const steps=ph==="STARTING"?ONLINE_STEPS:ph==="STOPPING"?OFFLINE_STEPS:null;
  show($("steps"),!!steps); show($("cancel"),ph==="STARTING");
  if(steps){const ol=$("steps");ol.innerHTML="";const at=steps.indexOf(S.step);
    steps.forEach((k,i)=>{const li=document.createElement("li");li.textContent=(i<at?"✓ ":i===at?"→ ":"· ")+t("step_"+k);
      li.className=i<at?"done":i===at?"now":"";ol.appendChild(li)})}
  const err=S.error||localErr;
  show($("error"),!!err); if(err)$("error").textContent=errText(err);
  show($("consent"),ph!=="ONLINE");
  if(!editing){$("device").value=S.device_label||"";$("plan").value=S.subscription_type||"PLUS"}
  const srv=S.server;
  show($("invite"),!!srv); show($("settings"),!!srv); show($("members"),!!srv); show($("tasks"),!!srv);
  if(srv){
    const g=srv.group;
    $("code").textContent=S.invite_code||"— — —";
    $("link").textContent=S.invite_link||t("invite_lost");
    $("copy").disabled=!S.invite_link;
    if(!editing){
      $("max_members").value=g.max_members; $("max_members").max=g.member_cap;
      $("max_members").min=Math.max(2,srv.members.length);
      $("max_tasks").value=g.max_tasks_per_day; $("max_minutes").value=Math.round(g.max_task_duration_seconds/60);
      $("auto").checked=srv.sharing?srv.sharing.auto_approve_members:true;
    }
    $("usage").textContent=(flash||t("usage",srv.tasks_started_last_24h,g.max_tasks_per_day))+t("cap",g.member_cap);
    const mb=$("member_rows"); mb.innerHTML="";
    srv.members.forEach(m=>{const tr=document.createElement("tr");
      const a=document.createElement("td");a.textContent=m.is_owner?t("you"):m.user_id.slice(0,8);
      const b=document.createElement("td");b.textContent=fmt(m.joined_at);
      const c=document.createElement("td");
      if(!m.is_owner){const btn=document.createElement("button");btn.className="s";btn.textContent=t("remove");
        btn.onclick=()=>{if(confirm(t("confirm_remove")))act("remove-member",{user_id:m.user_id})};c.append(btn)}
      tr.append(a,b,c);mb.append(tr)});
    const tb=$("task_rows"); tb.innerHTML="";
    srv.tasks.forEach(x=>{const tr=document.createElement("tr");
      const ap=x.approval_status==="APPROVED"?(x.approval_source==="OWNER_STANDING_APPROVAL"?t("approved_auto"):t("approved_owner")):x.approval_status==="PENDING"?t("pending"):t("rejected");
      [fmt(x.created_at),label("tt_",x.task_type)+" · "+label("m_",x.execution_mode),label("s_",x.status),ap].forEach(v=>{const td=document.createElement("td");td.textContent=v;tr.append(td)});
      tb.append(tr)});
  }
  const notes=[];
  if(srv&&!srv.live_ready)notes.push(t("live_blocked"));
  if(S.server_error)notes.push(t("e_server_unreachable")+S.server_error);
  if(!S.bundle_ok)notes.push(t("e_bundle_missing"));
  show($("notice"),notes.length>0); $("notice").textContent=notes.join(" ");
}
async function act(route, body){
  let ok=true;
  try{S=await call(route,body)}
  catch(e){ok=false;localErr={code:e.code,detail:e.message,server_code:e.server_code};
    setTimeout(()=>{localErr=null;render()},8000)}
  render();
  return ok;
}
async function poll(){try{S=await call("state");render()}catch(e){}}

$("lang").onclick=()=>{lang=lang==="zh"?"en":"zh";try{localStorage.setItem("sa_lang",lang)}catch(e){}applyStatic();render()};
$("big").onclick=()=>act("toggle",{device_label:$("device").value,subscription_type:$("plan").value});
$("cancel").onclick=()=>act("cancel");
$("rotate").onclick=()=>act("invite");
$("copy").onclick=async()=>{try{await navigator.clipboard.writeText(S.invite_link);$("copy").textContent=t("copied");
  setTimeout(()=>$("copy").textContent=t("copy"),1500)}catch(e){}};
function checkLimits(v){
  const lo=Math.max(2,S&&S.server?S.server.members.length:2), hi=S&&S.server?S.server.group.member_cap:v.max_members;
  if(![v.max_members,v.max_tasks_per_day,v.max_task_minutes].every(Number.isInteger))return t("r_integer");
  if(v.max_members<lo||v.max_members>hi)return t("r_members",lo,hi);
  if(v.max_tasks_per_day<1||v.max_tasks_per_day>50)return t("r_tasks");
  if(v.max_task_minutes<1||v.max_task_minutes>60)return t("r_minutes");
  return "";
}
function setFlash(text,ms){flash=text;render();if(ms)setTimeout(()=>{flash="";render()},ms)}
$("save").onclick=async()=>{
  const v={max_members:Number($("max_members").value),max_tasks_per_day:Number($("max_tasks").value),
    max_task_minutes:Number($("max_minutes").value),auto_approve_members:$("auto").checked};
  const bad=checkLimits(v);
  if(bad){setFlash(t("save_failed")+bad+" · ",6000);return}  // keep the user's edits on screen
  $("save").disabled=true;setFlash(t("saving")+" · ");
  const ok=await act("settings",v);
  $("save").disabled=false;
  if(ok){editing=false;setFlash(t("saved")+" · ",2000)}
  else{setFlash(t("save_failed").replace(/[：:\s]+$/,"")+" · ",8000)}  // reason is in the error box
};
$("dashboard").onclick=async()=>{try{const r=await call("dashboard");window.open(r.url,"_blank","noopener")}catch(e){localErr={code:e.code,detail:e.message};render()}};
["max_members","max_tasks","max_minutes","auto","device","plan"].forEach(id=>{$(id).addEventListener("focus",()=>editing=true);$(id).addEventListener("input",()=>editing=true)});
applyStatic(); poll(); setInterval(poll,1500);
</script>
</body>
</html>
"""
