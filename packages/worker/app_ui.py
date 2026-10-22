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
--accent-ink:#fff;--warn:#9a5b00;--warn-bg:#fff6e5;--err:#b3261e;--err-bg:#fdecea;--off:#8c877e;
--ok-bg:#e6f3ea;--run:#1f5f99;--run-bg:#e6eff8;--track:#e9e6df}
@media (prefers-color-scheme:dark){:root{--bg:#141412;--card:#1e1d1b;--ink:#f1efe9;--muted:#a19d95;
--line:#34322e;--accent:#3fb37a;--accent-ink:#08140e;--warn:#f0b35a;--warn-bg:#2b2416;--err:#ff8a80;
--err-bg:#2f1a18;--off:#6f6b64;--ok-bg:#183126;--run:#8cbcf0;--run-bg:#18273a;--track:#34322e}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,
"PingFang SC","Hiragino Sans GB","Segoe UI",sans-serif}
main{max-width:880px;margin:0 auto;padding:24px 16px 60px}
header{display:flex;justify-content:space-between;align-items:center;gap:12px}
h1{font-size:18px;margin:0;font-weight:600}h1 small{font-weight:400;font-size:14px}
h2{font-size:15px;margin:0;font-weight:600}
.muted{color:var(--muted)}small{color:var(--muted)}
.lang{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;
padding:4px 12px;cursor:pointer}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;margin-top:14px}
.head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:10px}
details>summary{cursor:pointer;font-weight:600}details[open]>summary{margin-bottom:12px}
.consent ul{margin:8px 0 0;padding-left:20px}.consent li{margin:4px 0}
.row{display:flex;gap:12px;flex-wrap:wrap}.row>label{flex:1 1 160px}
label{display:block;font-size:13px;color:var(--muted)}
input,select{width:100%;margin-top:4px;padding:8px 10px;border:1px solid var(--line);border-radius:8px;
background:var(--bg);color:var(--ink);font-size:15px}
input[type=checkbox]{width:auto;margin-right:6px}
button.s{border:1px solid var(--line);background:var(--bg);color:var(--ink);border-radius:8px;
padding:7px 14px;cursor:pointer;font-size:14px;white-space:nowrap}
button.s:disabled{opacity:.5;cursor:default}
button.p{background:var(--accent);color:var(--accent-ink);border-color:transparent;font-weight:600}
.status-title{font-size:19px;font-weight:600;display:flex;align-items:center;gap:10px}
.dot{width:10px;height:10px;border-radius:50%;background:var(--off);flex:none}
.dot.on{background:var(--accent)}.dot.busy{background:var(--warn);animation:blink 1.2s infinite}.dot.bad{background:var(--err)}
@keyframes blink{50%{opacity:.35}}
.status-sub{margin:2px 0 0 20px}
.gauges{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:22px;margin-top:16px;padding-top:14px;border-top:1px solid var(--line)}
.gauge .top{display:flex;justify-content:space-between;gap:8px;font-size:14px}
.bar{height:6px;border-radius:3px;background:var(--track);overflow:hidden;margin:6px 0 4px}
.bar i{display:block;height:100%;border-radius:3px;background:var(--accent)}
.bar i.warn{background:#d98b17}.bar i.bad{background:var(--err)}
.chip{display:inline-block;font-size:12px;padding:1px 9px;border-radius:999px;background:var(--track);color:var(--muted);white-space:nowrap}
.chip.ok{background:var(--ok-bg);color:var(--accent)}.chip.run{background:var(--run-bg);color:var(--run)}
.chip.wait{background:var(--warn-bg);color:var(--warn)}.chip.bad{background:var(--err-bg);color:var(--err)}
.pair{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr);gap:14px}
.pair>.card{margin-top:14px}
.li{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:9px 0;border-top:1px solid var(--line)}
.li .name{display:flex;align-items:center;gap:8px;min-width:0}
.li .name>span:last-child{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.av{width:26px;height:26px;border-radius:50%;background:var(--run-bg);color:var(--run);font-size:11px;display:inline-flex;
align-items:center;justify-content:center;flex:none}
.mini{display:inline-block;width:64px;height:4px;border-radius:2px;background:var(--track);vertical-align:middle;margin-left:6px;overflow:hidden}
.mini i{display:block;height:100%;background:var(--accent)}
.editor{display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;padding:0 0 10px 34px}
.editor label{flex:1 1 120px}
.code{font:600 22px/1.2 ui-monospace,Menlo,monospace;letter-spacing:2px;margin:2px 0 4px;word-break:break-all}
.link{font-size:13px;word-break:break-all}
.notice{background:var(--warn-bg);color:var(--warn);border-radius:10px;padding:10px 12px;margin-top:12px;font-size:14px}
.error{background:var(--err-bg);color:var(--err);border-radius:10px;padding:10px 12px;margin-top:12px;font-size:14px}
.steps{list-style:none;padding:0;margin:12px 0 0 20px}
.steps li{margin:3px 0;color:var(--muted)}.steps li.now{color:var(--ink);font-weight:600}
.steps li.done{color:var(--accent)}
.hidden{display:none!important}
.seg{display:flex;border:1px solid var(--line);border-radius:10px;overflow:hidden}
.seg button{flex:1;border:0;background:var(--bg);color:var(--ink);padding:9px 6px;font-size:14px;cursor:pointer}
.seg button+button{border-left:1px solid var(--line)}
.seg button.on{background:var(--accent);color:var(--accent-ink);font-weight:600}
h3.sub{font-size:14px;margin:16px 0 6px}
.checks label{display:flex;align-items:center;gap:6px;font-size:14px;color:var(--ink);margin:4px 0;flex-wrap:wrap}
.checks small{margin-left:4px}.checks button.s{padding:3px 10px;font-size:13px}
.dialog{margin-top:12px;border:1px solid var(--line);border-radius:12px;padding:14px;background:var(--bg)}
.foot{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-top:10px}
@media (max-width:640px){
.gauges{grid-template-columns:1fr;gap:12px}.pair{grid-template-columns:1fr;gap:0}
.status-title{font-size:17px}.li{flex-wrap:wrap}.editor{padding-left:0}
}
</style>
</head>
<body>
<main>
<header>
  <h1><span data-t="brand"></span> <small id="device_title"></small></h1>
  <button class="lang" id="lang" type="button"></button>
</header>

<section class="card consent hidden" id="consent">
  <h2 data-t="before_title"></h2>
  <label style="margin-top:10px"><span data-t="plan"></span>
    <select id="plan"><option value="PLUS">ChatGPT Plus</option><option value="PRO">ChatGPT Pro</option></select>
  </label>
  <p class="muted" data-t="consent_intro"></p>
  <ul>
    <li id="summary_tasks"></li>
    <li data-t="summary_credits"></li>
    <li id="summary_network"></li>
  </ul>
  <details style="margin-top:10px">
    <summary data-t="sharing_details"></summary>
    <ul class="ack"></ul>
  </details>
  <p class="muted" data-t="device_hint"></p>
  <button class="s p" id="go" type="button" data-t="first_online"></button>
</section>

<section class="card" id="status">
  <div class="head" style="align-items:flex-start">
    <div>
      <div class="status-title"><span class="dot" id="dot"></span><span id="state_title"></span></div>
      <div class="status-sub muted" id="state" role="status" aria-live="polite"></div>
    </div>
    <button class="s" id="big" type="button"></button>
  </div>
  <ol class="steps hidden" id="steps"></ol>
  <button class="s hidden" id="cancel" type="button" data-t="cancel" style="margin:8px 0 0 20px"></button>
  <div class="dialog hidden" id="offline_choice" role="dialog" aria-labelledby="oc_title">
    <h2 id="oc_title" data-t="oc_title"></h2>
    <p class="muted" id="oc_text"></p>
    <p class="muted" data-t="pause_note" style="font-size:13px"></p>
    <div class="row">
      <button class="s p" id="oc_finish" type="button" data-t="oc_finish"></button>
      <button class="s" id="oc_now" type="button" data-t="oc_now"></button>
      <button class="s" id="oc_cancel" type="button" data-t="cancel"></button>
    </div>
  </div>
  <div class="error hidden" id="error" role="alert"></div>
  <p class="muted hidden" id="retry_hint" data-t="retry_hint"></p>
  <div class="gauges hidden" id="gauges"></div>
</section>

<div class="pair">
  <section class="card hidden" id="members">
    <div class="head"><h2><span data-t="members_title"></span> <small id="members_count"></small></h2><small data-t="m_used"></small></div>
    <div id="member_rows"></div>
  </section>
  <section class="card hidden" id="invite">
    <h2 data-t="invite_title"></h2>
    <div class="code" id="code" style="margin-top:8px"></div>
    <small id="invite_sub"></small>
    <div class="row" style="margin-top:12px;gap:8px">
      <button class="s p" id="copy" type="button" data-t="copy"></button>
      <button class="s" id="rotate" type="button" data-t="rotate"></button>
    </div>
    <details style="margin-top:12px">
      <summary data-t="show_link" style="font-weight:400;font-size:13px"></summary>
      <div class="link muted" id="link"></div>
      <div class="link muted hidden" id="links" style="margin-top:6px"></div>
      <p class="muted" data-t="invite_help" style="font-size:13px"></p>
    </details>
  </section>
</div>

<section class="card hidden" id="tasks">
  <h2 data-t="tasks_title"></h2>
  <div id="task_rows" style="margin-top:8px"></div>
  <div class="foot"><small id="tasks_empty" class="hidden" data-t="tasks_none"></small>
    <button class="s" id="dashboard" type="button" data-t="open_dashboard"></button></div>
</section>

<section class="card hidden" id="settings">
  <div class="head"><h2 data-t="settings_title"></h2><small data-t="anytime"></small></div>
  <div class="seg" id="presets" role="group"></div>
  <p class="muted" id="preset_summary" style="font-size:13px;margin:8px 0 0"></p>
  <details id="custom" style="margin-top:12px">
    <summary data-t="customize" style="font-weight:400;font-size:14px"></summary>
    <div class="row">
      <label><span data-t="max_members"></span><input id="max_members" type="number" min="2"></label>
      <label><span data-t="max_tasks"></span><input id="max_tasks" type="number" min="1" max="50"></label>
      <label><span data-t="max_minutes"></span><input id="max_minutes" type="number" min="1" max="60"></label>
      <label><span data-t="concurrency"></span><input id="concurrency" type="number" min="1" max="3"></label>
    </div>
    <h3 class="sub" data-t="kinds_title"></h3>
    <div id="kinds" class="checks"></div>
    <h3 class="sub" data-t="tools_title"></h3>
    <div id="tools" class="checks"></div>
    <label style="margin-top:10px"><input id="auto" type="checkbox"><span data-t="auto_approve"></span></label>
    <div class="row" style="margin-top:10px;align-items:center">
      <button class="s p" id="save" type="button" data-t="save"></button>
      <small id="usage" role="status" aria-live="polite"></small>
    </div>
  </details>
</section>

<details class="card" id="connection_options">
  <summary data-t="connection_options"></summary>
  <div class="row">
    <label><span data-t="device_name"></span><input id="device" maxlength="80"></label>
    <label id="net_box" class="hidden"><span data-t="net_label"></span><select id="net"></select></label>
  </div>
  <p class="muted hidden" id="net_note"></p>
</details>
<details class="card hidden" id="terms">
  <summary data-t="sharing_details"></summary>
  <ul class="ack" style="padding-left:20px;margin:0"></ul>
</details>

<div class="notice hidden" id="notice" role="status" aria-live="polite"></div>
<p><small id="footer" data-t="footer"></small></p>
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
 step_codex_install:"检查 / 安装 Codex CLI（首次需下载约 100 MB）", step_git_check:"检查 Apple 命令行工具（仅代码任务需要，缺少时不影响上线）", step_codex_login:"登录你自己的 ChatGPT（浏览器会自动打开）",
 step_signup:"注册这台设备并创建你的小组", step_owner_check:"本机自检（用你自己的额度跑一句很短的提示）",
 step_server_online:"开启分享与自动批准", step_worker_start:"启动接单程序",
 step_server_offline:"暂停分享", step_worker_stop:"停止接单程序",
 before_title:"上线前请确认", device_name:"设备名称（小组成员会看到）", plan:"你的订阅",
 first_online:"同意并上线",
 consent_intro:"阅读下面的分享说明。点击上线即表示你本人同意，随时可以暂停：",
 sharing_details:"完整分享说明", connection_options:"设备名称与连接方式（通常无需修改）",
 device_hint:"设备名称已自动填写，支持中文和空格；连接方式默认自动选择。",
 summary_tasks_auto:"账号留在本机。成员的任务会在你允许的范围和上限内自动批准，在这台 Mac 上执行并消耗你的订阅额度。",
 summary_tasks_manual:"账号留在本机。成员的任务经你批准后，在允许的范围和上限内使用这台 Mac 和你的订阅额度执行。",
 summary_credits:"请确认：账号没有额外购买或赠送的 Credits，自动充值已关闭。首次上线会消耗少量订阅额度完成自检。",
 summary_network_on:"允许成员勾选「需要联网」的任务联网，网站可见你的 IP，任务可能访问局域网设备。可在「分享设置」中关闭。",
 summary_network_off:"已关闭任务联网；成员的任务只能使用上传的材料。可在「分享设置」中调整。",
 retry_hint:"修复上面的提示后，点「重试上线」。已安装的组件和已通过的自检会保留。",
 ack_1:"这是私人、非商业的实验，没有任何付费、转售或报酬。",
 ack_2:"我的 ChatGPT 账号只在这台电脑的官方 Codex CLI 里登录，密码和登录凭据不会交给 SpareAgent 或任何人。",
 ack_3:"小组成员提交的任务会在设置的上限内自动批准，并使用我自己的订阅额度在这台电脑上运行（只限我允许的任务种类和工具，在隔离的临时目录中执行；仅在成员勾选且你允许时联网）。",
 ack_4:"我的账号没有额外购买或赠送的 Credits，并已关闭自动充值；分享只消耗订阅自带额度。",
 ack_5:"首次上线时会用我的额度运行一次很短的自检提示；通过后不重复运行。",
 ack_6:"我可以随时调整人数和任务上限、移除成员、关闭自动批准或下线。",
 ack_7:"服务器运营者设置的总上限（例如小组最多人数）仍然适用。",
 ack_8:"默认允许成员的任务联网（勾选了「需要联网」的任务）：网站会看到你的 IP，任务能访问你家里网络中的设备。不想要可以在「本机工具」里取消「允许联网」。",
 invite_title:"邀请朋友", copy:"复制邀请说明", copied:"已复制", rotate:"换一个",
 invite_help:"把邀请链接或邀请码私下发给你信任的人。他们打开链接、勾选同意后即可加入并提交任务。换码后旧码立即失效。",
 invite_lost:"邀请码只在生成时显示一次。点「换一个邀请码」生成新的。",
 limits_title:"小组与任务设置", max_members:"小组人数上限（含你自己）",
 max_tasks:"每 24 小时最多任务数", max_minutes:"单个任务最长（分钟）",
 auto_approve:"自动批准小组成员的任务", save:"保存", saved:"已保存",
 usage:(n,m)=>`过去 24 小时已接 ${n} / ${m} 个任务`, cap:(c)=>`（服务器上限 ${c} 人）`,
 members_title:"成员", member:"成员", joined:"加入时间", you:"你", remove:"移除",
 m_used:"过去 24 小时", m_used_val:(n,m)=>`${n} 个任务 · ${m} 分钟`, m_task_cap:"每日任务上限", m_min_cap:"每日分钟上限",
 m_no_cap:"不单独限制", m_cap_range:"任务上限 1–50，分钟上限 1–1440，留空表示不单独限制。",
 m_caps_help:"按成员单独限额：在你这台电脑上，每个成员每 24 小时最多用多少个任务、多少分钟执行时间。留空表示只受上面的整体上限约束。超出后，该成员的新任务会排队并显示原因，额度恢复或你调高后自动继续；正在运行的任务不受影响。",
 confirm_remove:"移除该成员？其未完成的任务会被取消，邀请码也会更换。",
 tasks_title:"最近的任务", time:"时间", kind:"类型", status:"状态", approval:"批准",
 approved_auto:"自动批准", approved_owner:"你批准", pending:"等待批准", rejected:"已拒绝",
 open_dashboard:"打开网页控制台",
 live_blocked:"注意：服务器暂未开启真实执行，任务会排队等待，直到运营者开启。",
 footer:"关闭这个页面不会下线；关闭运行它的终端窗口或按 Ctrl-C 会自动下线。",
 footer_app:"关闭这个页面会继续运行。可从菜单栏打开控制页、暂停分享或退出 SpareAgent。",
 e_bundle_missing:"缺少运营者提供的连接配置（bundle.json）。请用运营者给你的安装包重新安装。", net_label:"连接方式", net_auto:"自动（先试可用的）", net_ok:"可连接", net_down:"连不上",
 net_using:(l)=>`当前使用：${l}`, net_hint:"局域网只在同一个 Wi‑Fi 下可用；不在一起时用 Tailscale（双方都要登录 Tailscale）。下线后才能切换。",
 e_network_locked:"请先下线，再切换连接方式。", links_title:"其他网络的链接：",
 e_codex_missing:"没有找到 Codex CLI，也没有 npm 或 Homebrew 可以自动安装。请先安装 Node.js（nodejs.org）后重试。",
 e_codex_install_failed:"自动安装 Codex CLI 失败，请检查网络后点「重试上线」。",
 e_git_missing:"需要安装 Apple 命令行工具：请在弹出的窗口点「安装」，完成后（约 5–15 分钟）点「重试上线」。", e_codex_login_failed:"Codex 登录没有完成。",
 e_codex_login_required:"需要用你自己的 ChatGPT 账号登录 Codex（不要用 API Key）。",
 e_owner_check_failed:"本机自检没有通过。请确认 Codex 能正常使用后重试。",
 e_server_rejected:"服务器拒绝了请求：", e_server_unreachable:"连不上服务器，请检查网络：",
 e_tailscale_unreachable:"无法连接 Tailscale 私网地址。请打开 Tailscale，确认已登录并连接到运营者的私网；若已连接，请联系运营者检查服务。",
 e_certificate_invalid:"HTTPS 证书与地址不匹配或不受信任。请向运营者获取最新安装包；不要忽略证书警告。",
 e_service_unreachable:"服务器没有响应或尚未就绪。请稍后重试；若仍失败，请联系运营者检查服务。",
 e_worker_stopped:"接单程序意外停止。", e_not_registered:"请先上线一次。",
 e_plan_invalid:"请选择订阅类型。", e_invalid_input:"输入有误：", e_unexpected:"发生意外错误：",
 s_QUEUED:"排队中", s_RESERVED:"已分配", s_RUNNING:"运行中", s_VERIFYING:"校验中", s_SUCCEEDED:"完成",
 s_FAILED:"失败", s_CANCELLED:"已取消", s_CANCELLING:"取消中", s_TIMED_OUT:"超时", s_CREATED:"已创建",
 s_AWAITING_APPROVAL:"等待批准", s_REJECTED:"已拒绝", s_RETRY_PENDING:"等待重新运行",
 tt_CODE_REVIEW:"代码审查", tt_TRANSLATE_DOCUMENT:"翻译文档", tt_ANALYZE_SPREADSHEET:"分析表格", tt_CODE_CHANGE:"修改代码", tt_GENERAL:"通用任务",
 tt_CODE_FIX:"修复问题", tt_WRITE_TESTS:"补充测试",
 m_LIVE:"真实执行", m_SIMULATED:"模拟",
 save_failed:"未保存：", saving:"正在保存…",
 concurrency:"同时运行的任务数（1–3）",
 oc_title:"还有任务正在运行", oc_text:(n)=>`这台电脑上有 ${n} 个任务正在运行。下线后不会再接新任务。`,
 oc_finish:"运行完再下线", oc_now:"立即停止并交还", step_draining:"等待正在运行的任务完成",
 running_title:"正在运行", elapsed:"已用时间", progress:"进度", none_running:"现在没有正在运行的任务。",
 usage_line:(n,m,r,q)=>`过去 24 小时：接了 ${n} 个任务，执行了 ${m} 分钟。现在运行 ${r} 个，排队 ${q} 个。`,
 pause_note:"暂停或下线后：不再接新任务；正在运行的任务按你选择的方式处理（运行完；或立即停止并交还队列，每个任务最多重新运行一次，10 分钟内你重新上线就会继续，否则按超时结束）；排队中的任务会等你重新上线。",
 queued_n:(n)=>`排队第 ${n} 位`,
 fc_INPUT:"输入不合格", fc_TIMEOUT:"超时", fc_WAIT_TIMEOUT:"等待接单超时", fc_OUTPUT_LIMIT:"结果超出大小上限",
 fc_DEVICE_INTERRUPTED:"设备中断", fc_CHECK_FAILED:"结果未通过检查", fc_AUTHORIZATION:"授权或限额变化",
 fc_LOGIN:"Codex 登录失效", fc_USAGE_LIMIT:"订阅额度已用完", fc_CANCELLED:"已取消", fc_REFUSED:"Codex 拒绝执行", fc_OTHER:"其他原因", r_concurrency:"同时运行的任务数需在 1–3 之间。", r_kinds:"至少选择一种任务。",
 kinds_title:"允许的任务种类", tools_title:"本机工具（任务只能使用你允许且已安装的工具）",
 ks_OPEN:"已开放", ks_LIVE_ACCEPTED:"验收通过，待开放", ks_SIMULATED_OK:"模拟通过", ks_DEVELOPING:"开发中，仅模拟", ks_DISABLED:"已停用",
 needs:"需要：", missing_tool:"（本机缺少）",
 tool_git:"Git（代码任务）", tool_doc:"文档处理（内置）", tool_code:"Codex 执行（内置）", tool_network:"允许联网（成员勾选「需要联网」的任务）",
 network_hint:"默认开启。这类任务可以搜索和浏览网页；仍然读不到你电脑上的其他文件。不想要就取消勾选并保存。",
 network_confirm:"允许联网后，成员的任务可以访问互联网：网站会看到你的 IP 地址，任务也能访问你家里网络中的其他设备，恶意需求还可能诱导 Codex 把成员上传的材料发给别人（你电脑上的其他文件仍然读不到）。确定允许吗？", "tool_python-data":"数据处理（pandas、matplotlib、openpyxl）",
 installed:"已安装", not_installed:"未安装", installing:"正在安装…", install_failed:"安装失败：",
 install_btn:(n)=>`安装（约 ${n} MB）`, remove_btn:"删除", git_hint:"未安装时仍可上线，只接文档和表格任务；上线时会弹出 Apple 命令行工具安装窗口，装好后自动接代码任务", allow:"允许",
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
 sc_UNAUTHENTICATED:"设备凭据已失效，请重新上线。", sc_RATE_LIMITED:"操作太频繁，请稍后再试。",
 brand:"SpareAgent", btn_pause:"暂停分享",
 title_OFFLINE:"已暂停分享", title_ONLINE:"在线，正在分享", title_STARTING:"正在上线…", title_STOPPING:"正在下线…", title_ERROR:"上线没有完成",
 sub_OFFLINE:"不会接任何任务。点「上线」开始分享。", sub_ONLINE:"成员的任务会自动批准，在这台 Mac 上运行。",
 sub_ONLINE_manual:"成员的任务经你批准后，在这台 Mac 上运行。", sub_STARTING:"请按下面的提示操作。", sub_STOPPING:"已停止接新任务。",
 sub_ERROR:"修复下面的问题后重试。", sub_FIRST:"看完上面的说明，点「同意并上线」。",
 quota_title:"订阅额度", left:(p)=>`剩 ${p}%`, win_week:"本周额度", win_days:(d)=>`${d} 天额度`, win_hours:(h)=>`${h} 小时额度`, win_minutes:(m)=>`${m} 分钟额度`,
 reset_min:(m)=>`${m} 分钟后重置`, reset_hm:(h,m)=>`${h} 小时 ${m} 分后重置`, reset_at:(s)=>`${s} 重置`,
 quota_fb_ok:"正常（暂时读不到具体百分比）", quota_fb_low:"快用完了", quota_fb_exhausted:"已用完，恢复后再接任务",
 now_title:"现在", now_idle:"空闲", now_running:(n)=>`运行 ${n}`, now_sub:(q,n,m)=>`排队 ${q} · 24 小时内 ${n} 个任务 · ${m} 分钟`,
 members_count:(n,m)=>`${n} / ${m}`, owner_name:"你（主人）", member_name:(id)=>`成员 ${id}`, m_used_short:(n,m)=>`${n} 个 · ${m} 分钟`,
 m_capped:"已单独限额", limits_btn:"限额",
 invite_sub_public:"朋友用浏览器打开链接、勾选同意就能加入。", invite_sub_tailscale:"朋友先登录 Tailscale，再打开链接、勾选同意加入。",
 show_link:"显示链接", tasks_none:"还没有任务。",
 settings_title:"分享设置", anytime:"随时可改", preset_save:"省着用", preset_standard:"标准", preset_open:"放开用", preset_custom:"自定义",
 preset_summary:(n,m,c,net,k,all)=>`每天最多 ${n} 个任务 · 单个最长 ${m} 分钟 · 同时 ${c} 个 · ${net?"允许联网":"不联网"} · ${k===all?`全部 ${all} 类任务`:`${k} / ${all} 类任务`}`,
 customize:"自定义每一项、任务种类和本机工具",
 inv_head:"我在用 SpareAgent 和几个朋友共享我的 ChatGPT 额度，邀请你加入：",
 inv_step_tailscale:"先安装 Tailscale 并登录，加入运营者的私网（运营者会给你邀请）。",
 inv_step_open:(l)=>`打开这个链接，勾选同意即可加入：${l}`,
 inv_step_browser:(l)=>`用浏览器打开这个链接（手机或电脑都可以，不用装任何软件），勾选同意即可加入：${l}`,
 inv_step_submit:"在网页里提交任务，结果做好后可以直接下载。", inv_tail:"这个链接只给你，请不要转发。"
},
en: {
 title:"SpareAgent · One-click sharing", subtitle:"Share your spare ChatGPT Codex capacity with your own group",
 lang:"中文", cancel:"Cancel",
 go_online:"Go online", go_online_sub:"Start sharing", online:"Online", online_sub:"Click to go offline",
 starting:"Going online…", stopping:"Going offline…", retry:"Retry",
 st_OFFLINE:"Offline: no tasks will run.", st_ONLINE:"Online: your group's tasks are auto-approved and run on this computer.",
 st_STARTING:"Going online — follow any prompts.", st_STOPPING:"Going offline…", st_ERROR:"Could not go online: ",
 step_codex_install:"Check / install the Codex CLI (first time: ~100 MB download)", step_git_check:"Check Apple command line tools (only code tasks need them; going online works without)", step_codex_login:"Sign in with your own ChatGPT (a browser opens)",
 step_signup:"Register this device and create your group", step_owner_check:"Self-check (one tiny prompt on your own plan)",
 step_server_online:"Enable sharing and automatic approval", step_worker_start:"Start the worker",
 step_server_offline:"Pause sharing", step_worker_stop:"Stop the worker",
 before_title:"Before you go online", device_name:"Device name (visible to your group)", plan:"Your plan",
 first_online:"Agree and go online",
 consent_intro:"Read the sharing terms below. Going online confirms your agreement; you can pause at any time:",
 sharing_details:"Full sharing terms", connection_options:"Device name and connection (usually no changes needed)",
 device_hint:"The device name is filled in automatically and supports Chinese and spaces. The connection defaults to automatic.",
 summary_tasks_auto:"Credentials stay on this Mac. Members' tasks are automatically approved within your allowed scope and limits, run on this Mac and consume your plan usage.",
 summary_tasks_manual:"Credentials stay on this Mac. After your approval, members' tasks run on this Mac using your plan, within your allowed scope and limits.",
 summary_credits:"Confirm that your account has no additional purchased or granted Credits and auto-reload is off. The first startup uses a little plan usage for a self-check.",
 summary_network_on:"Tasks marked “needs internet” may access the internet: websites see your IP and tasks may reach devices on your local network. Turn this off in “Sharing settings”.",
 summary_network_off:"Task internet access is off; tasks can only use uploaded materials. Change this in “Sharing settings”.",
 retry_hint:"Resolve the issue above, then retry. Installed components and a successful self-check are kept.",
 ack_1:"This is a private, non-commercial experiment with no payment, resale or compensation.",
 ack_2:"My ChatGPT account is signed in only in the official Codex CLI on this computer; my credentials are never given to SpareAgent or anyone.",
 ack_3:"Tasks from my group's members are approved automatically within my limits and run on this computer with my own plan usage (only the kinds and tools I allow, in an isolated temporary folder; network access requires both your permission and the member’s request).",
 ack_4:"My account holds no additional purchased or granted Credits and auto-reload is off; sharing only uses included plan usage.",
 ack_5:"The first startup runs one very short self-check prompt on my plan; a successful check is reused.",
 ack_6:"I can change member and task limits, remove members, switch off automatic approval or go offline at any time.",
 ack_7:"The server operator's overall caps (such as the maximum group size) still apply.",
 ack_8:"Members' tasks may use the internet by default (those marked \u201cneeds internet\u201d): websites see your IP and tasks can reach devices on your home network. Untick \u201cAllow internet\u201d under device tools to turn it off.",
 invite_title:"Invite friends", copy:"Copy invitation", copied:"Copied", rotate:"New code",
 invite_help:"Send the link or code privately to people you trust. They open it, accept the terms and can submit tasks. A new code immediately invalidates the old one.",
 invite_lost:"The code is shown only when it is created. Click “New invite code” to issue another.",
 limits_title:"Group and task settings", max_members:"Maximum members (including you)",
 max_tasks:"Maximum tasks per 24 hours", max_minutes:"Maximum minutes per task",
 auto_approve:"Approve my group's tasks automatically", save:"Save", saved:"Saved",
 usage:(n,m)=>`${n} / ${m} tasks in the last 24 hours`, cap:(c)=>` (server cap ${c})`,
 members_title:"Members", member:"Member", joined:"Joined", you:"You", remove:"Remove",
 m_used:"Last 24 h", m_used_val:(n,m)=>`${n} tasks · ${m} min`, m_task_cap:"Tasks per day", m_min_cap:"Minutes per day",
 m_no_cap:"no own cap", m_cap_range:"Tasks 1–50, minutes 1–1440; leave empty for no own cap.",
 m_caps_help:"Per-member caps on this Mac per rolling 24 hours. Empty means only the overall limits above apply. Over a cap, that member's new tasks wait with the reason and continue when usage frees up or you raise the cap; running tasks are not affected.",
 confirm_remove:"Remove this member? Their unfinished tasks are cancelled and the invite code changes.",
 tasks_title:"Recent tasks", time:"Time", kind:"Type", status:"Status", approval:"Approval",
 approved_auto:"Automatic", approved_owner:"By you", pending:"Pending", rejected:"Rejected",
 open_dashboard:"Open web dashboard",
 live_blocked:"Note: real execution is not enabled on the server yet; tasks wait until the operator enables it.",
 footer:"Closing this page does not go offline; closing the Terminal window that runs it, or Ctrl-C, does.",
 footer_app:"Closing this page keeps SpareAgent running. Use the menu bar to open the control page, pause sharing or quit.",
 e_bundle_missing:"The operator's connection file (bundle.json) is missing. Reinstall from the operator's package.", net_label:"Connection", net_auto:"Automatic (first that works)", net_ok:"reachable", net_down:"unreachable",
 net_using:(l)=>`Using: ${l}`, net_hint:"The LAN address works only on the same Wi-Fi; elsewhere use Tailscale (both people signed in to Tailscale). Switch while offline.",
 e_network_locked:"Go offline before switching the connection.", links_title:"Links for other networks:",
 e_codex_missing:"Codex CLI not found and neither npm nor Homebrew is available to install it. Install Node.js (nodejs.org) and retry.",
 e_codex_install_failed:"Installing the Codex CLI failed; check the network and retry.",
 e_git_missing:"Apple's command line tools are needed: click Install in the dialog, then Retry when it finishes (about 5–15 minutes).", e_codex_login_failed:"Codex sign-in did not finish.",
 e_codex_login_required:"Sign in to Codex with your own ChatGPT account (not an API key).",
 e_owner_check_failed:"The self-check failed. Make sure Codex works, then retry.",
 e_server_rejected:"The server rejected the request: ", e_server_unreachable:"Cannot reach the server; check the network: ",
 e_tailscale_unreachable:"Cannot reach the Tailscale address. Open Tailscale and check you are signed in and connected to the operator’s private network. If connected, ask the operator to check the service.",
 e_certificate_invalid:"The HTTPS certificate does not match the address or is not trusted. Ask the operator for an updated package; do not bypass certificate warnings.",
 e_service_unreachable:"The service is unavailable or not ready. Retry shortly; if it still fails, ask the operator to check it.",
 e_worker_stopped:"The worker stopped unexpectedly.", e_not_registered:"Go online once first.",
 e_plan_invalid:"Choose your plan.", e_invalid_input:"Invalid input: ", e_unexpected:"Unexpected error: ",
 s_QUEUED:"Queued", s_RESERVED:"Assigned", s_RUNNING:"Running", s_VERIFYING:"Verifying", s_SUCCEEDED:"Done",
 s_FAILED:"Failed", s_CANCELLED:"Cancelled", s_CANCELLING:"Cancelling", s_TIMED_OUT:"Timed out", s_CREATED:"Created",
 s_AWAITING_APPROVAL:"Awaiting approval", s_REJECTED:"Rejected", s_RETRY_PENDING:"Waiting to re-run",
 tt_CODE_REVIEW:"Code review", tt_TRANSLATE_DOCUMENT:"Translate a document", tt_ANALYZE_SPREADSHEET:"Analyze spreadsheets", tt_CODE_CHANGE:"Change code", tt_GENERAL:"General task",
 tt_CODE_FIX:"Fix a bug", tt_WRITE_TESTS:"Write tests",
 m_LIVE:"Live", m_SIMULATED:"Simulated",
 save_failed:"Not saved: ", saving:"Saving…",
 concurrency:"Tasks at the same time (1–3)",
 oc_title:"Tasks are still running", oc_text:(n)=>`${n} task(s) are running on this computer. Going offline accepts no new tasks.`,
 oc_finish:"Finish them, then go offline", oc_now:"Stop now and hand back", step_draining:"Waiting for running tasks to finish",
 running_title:"Running now", elapsed:"Elapsed", progress:"Progress", none_running:"Nothing is running right now.",
 usage_line:(n,m,r,q)=>`Last 24 hours: ${n} task(s), ${m} minutes of execution. Running ${r}, queued ${q}.`,
 pause_note:"After pausing or going offline: no new tasks; running tasks either finish, or stop and go back to the queue (each task is re-run at most once; it resumes if you are back online within 10 minutes, otherwise it times out); queued tasks wait until you are online again.",
 queued_n:(n)=>`Queued #${n}`,
 fc_INPUT:"Invalid input", fc_TIMEOUT:"Timed out", fc_WAIT_TIMEOUT:"Not picked up in time", fc_OUTPUT_LIMIT:"Result too large",
 fc_DEVICE_INTERRUPTED:"Device interrupted", fc_CHECK_FAILED:"Result failed its checks", fc_AUTHORIZATION:"Authorization or limits changed",
 fc_LOGIN:"Codex sign-in expired", fc_USAGE_LIMIT:"Plan usage limit reached", fc_CANCELLED:"Cancelled", fc_REFUSED:"Codex declined", fc_OTHER:"Other reason", r_concurrency:"Tasks at the same time must be 1–3.", r_kinds:"Choose at least one kind.",
 kinds_title:"Allowed task kinds", tools_title:"Tools on this device (tasks use only tools you allow and have installed)",
 ks_OPEN:"Open", ks_LIVE_ACCEPTED:"Accepted, not yet open", ks_SIMULATED_OK:"Simulation passed", ks_DEVELOPING:"In development, simulation only", ks_DISABLED:"Disabled",
 needs:"Needs: ", missing_tool:" (missing here)",
 tool_git:"Git (code tasks)", tool_doc:"Documents (built in)", tool_code:"Codex executor (built in)", tool_network:"Allow internet (tasks whose member ticked “needs internet”)",
 network_hint:"On by default. Those tasks can search and read the web; they still cannot read other files on this Mac. Untick and save to turn it off.",
 network_confirm:"With internet allowed, members' tasks can reach the internet: websites see your IP address, tasks can reach other devices on your home network, and a malicious request could get Codex to send the member's uploaded files elsewhere (other files on this Mac stay unreadable). Allow it?", "tool_python-data":"Data tools (pandas, matplotlib, openpyxl)",
 installed:"Installed", not_installed:"Not installed", installing:"Installing…", install_failed:"Install failed: ",
 install_btn:(n)=>`Install (~${n} MB)`, remove_btn:"Remove", git_hint:"You can go online without them and take document and table tasks; going online offers the Apple command line tools, and code tasks start once they are installed", allow:"Allow",
 r_members:(a,b)=>`Members must be between ${a} and ${b}.`, r_tasks:"Tasks per 24 hours must be between 1 and 50.",
 r_minutes:"Minutes per task must be between 1 and 60.", r_integer:"Enter whole numbers.",
 sc_code:(c)=>` (error code ${c})`,
 brand:"SpareAgent", btn_pause:"Pause sharing",
 title_OFFLINE:"Not sharing", title_ONLINE:"Online, sharing", title_STARTING:"Going online…", title_STOPPING:"Going offline…", title_ERROR:"Couldn't go online",
 sub_OFFLINE:"No tasks will run. Click “Go online” to start sharing.", sub_ONLINE:"Your group's tasks are approved automatically and run on this Mac.",
 sub_ONLINE_manual:"Your group's tasks run on this Mac after you approve them.", sub_STARTING:"Follow the steps below.", sub_STOPPING:"No new tasks are accepted.",
 sub_ERROR:"Fix the issue below, then retry.", sub_FIRST:"Read the terms above, then click “Agree and go online”.",
 quota_title:"Plan usage", left:(p)=>`${p}% left`, win_week:"This week", win_days:(d)=>`${d}-day window`, win_hours:(h)=>`${h}-hour window`, win_minutes:(m)=>`${m}-minute window`,
 reset_min:(m)=>`resets in ${m} min`, reset_hm:(h,m)=>`resets in ${h} h ${m} min`, reset_at:(s)=>`resets ${s}`,
 quota_fb_ok:"OK (exact percentages unavailable)", quota_fb_low:"Running low", quota_fb_exhausted:"Used up; tasks resume after it resets",
 now_title:"Now", now_idle:"Idle", now_running:(n)=>`${n} running`, now_sub:(q,n,m)=>`${q} queued · ${n} tasks, ${m} min in 24 h`,
 members_count:(n,m)=>`${n} / ${m}`, owner_name:"You (owner)", member_name:(id)=>`Member ${id}`, m_used_short:(n,m)=>`${n} tasks · ${m} min`,
 m_capped:"own cap", limits_btn:"Limits",
 invite_sub_public:"Friends open the link in a browser and accept the terms to join.", invite_sub_tailscale:"Friends sign in to Tailscale, then open the link and accept the terms.",
 show_link:"Show link", tasks_none:"No tasks yet.",
 settings_title:"Sharing settings", anytime:"Change any time", preset_save:"Light", preset_standard:"Standard", preset_open:"Generous", preset_custom:"Custom",
 preset_summary:(n,m,c,net,k,all)=>`Up to ${n} tasks a day · ${m} min each · ${c} at a time · ${net?"internet allowed":"no internet"} · ${k===all?`all ${all} kinds`:`${k} of ${all} kinds`}`,
 customize:"Customize each limit, task kinds and device tools",
 inv_head:"I'm sharing my ChatGPT plan with a few friends through SpareAgent. Join us:",
 inv_step_tailscale:"Install Tailscale, sign in and join the operator's private network (the operator will invite you).",
 inv_step_open:(l)=>`Open this link and accept the terms to join: ${l}`,
 inv_step_browser:(l)=>`Open this link in a browser (phone or computer, nothing to install) and accept the terms to join: ${l}`,
 inv_step_submit:"Submit tasks on the web page and download the results when they are ready.", inv_tail:"This link is just for you; please don't forward it."
}};
const ONLINE_STEPS=["codex_install","git_check","codex_login","signup","owner_check","server_online","worker_start"];
const OFFLINE_STEPS=["server_offline","draining","worker_stop"];
const KEY=new URLSearchParams(location.hash.slice(1)).get("k")||"";
let lang=(()=>{try{return localStorage.getItem("sa_lang")}catch(e){return null}})()||"zh";
let S=null, onboardingEditing=false, settingsEditing=false, flash="", localErr=null;
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
  document.querySelectorAll(".ack").forEach(ul=>{ul.innerHTML="";
    for(let i=1;i<=8;i++){const li=document.createElement("li");li.textContent=t("ack_"+i);ul.appendChild(li)}});
}
function el(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e}
function render(){
  if(!S){if(localErr){show($("error"),true);$("error").textContent=errText(localErr)}return}
  const ph=S.phase, srv=S.server, sharing=srv&&srv.sharing, busy=ph==="STARTING"||ph==="STOPPING";
  $("footer").textContent=t(S.desktop_app?"footer_app":"footer");
  $("device_title").textContent="· "+(S.device_label||"");
  // First run: the terms and plan are a step before going online, not part of the daily page.
  const onboarding=!S.consented&&ph!=="ONLINE";
  show($("consent"),onboarding); show($("terms"),!onboarding);
  $("go").disabled=busy||!KEY;
  const big=$("big");
  big.className="s"+(ph==="OFFLINE"||ph==="ERROR"?" p":"");
  show(big,!onboarding||ph==="ERROR");
  big.disabled=busy||!KEY;
  big.textContent=ph==="ONLINE"?t("btn_pause"):ph==="ERROR"?t("retry"):busy?t(ph==="STARTING"?"starting":"stopping"):t("go_online");
  $("dot").className="dot"+(ph==="ONLINE"?" on":busy?" busy":ph==="ERROR"?" bad":"");
  $("state_title").textContent=t("title_"+ph);
  $("state").textContent=onboarding&&ph==="OFFLINE"?t("sub_FIRST"):ph==="ONLINE"&&sharing&&sharing.auto_approve_members===false?t("sub_ONLINE_manual"):t("sub_"+ph);
  const steps=ph==="STARTING"?ONLINE_STEPS:ph==="STOPPING"?OFFLINE_STEPS:null;
  show($("steps"),!!steps); show($("cancel"),ph==="STARTING");
  if(steps){const ol=$("steps");ol.innerHTML="";const at=steps.indexOf(S.step);
    steps.forEach((k,i)=>{const li=el("li",i<at?"done":i===at?"now":"",(i<at?"✓ ":i===at?"→ ":"· ")+t("step_"+k));ol.appendChild(li)})}
  const err=S.error||localErr;
  show($("error"),!!err); if(err)$("error").textContent=errText(err);
  show($("retry_hint"),ph==="ERROR");
  if(ph==="ONLINE")onboardingEditing=false;
  if(!onboardingEditing){if(document.activeElement!==$("device"))$("device").value=S.device_label||"";$("plan").value=S.subscription_type||"PLUS"}
  $("device").disabled=$("plan").disabled=busy;
  renderNetworks(ph);
  $("summary_tasks").textContent=t(sharing&&sharing.auto_approve_members===false?"summary_tasks_manual":"summary_tasks_auto");
  $("summary_network").textContent=t(sharing&&!(sharing.allowed_tools||[]).includes("network")?"summary_network_off":"summary_network_on");
  renderGauges(srv);
  ["invite","settings","members","tasks"].forEach(id=>show($(id),!!srv));
  if(srv){
    const g=srv.group;
    $("code").textContent=S.invite_code||"— — —";
    $("invite_sub").textContent=S.invite_link?t(srv.public_dashboard?"invite_sub_public":"invite_sub_tailscale"):t("invite_lost");
    $("link").textContent=S.invite_link||"";
    $("copy").disabled=!S.invite_link;
    const others=(S.invite_links||[]).filter(l=>l.id!==S.network);
    show($("links"),others.length>0);
    $("links").textContent=others.length?t("links_title")+" "+others.map(l=>l.label+": "+l.url).join("  "):"";
    if(!settingsEditing){
      $("max_members").value=g.max_members; $("max_members").max=g.member_cap;
      $("max_members").min=Math.max(2,srv.members.length);
      $("max_tasks").value=g.max_tasks_per_day; $("max_minutes").value=Math.round(g.max_task_duration_seconds/60);
      $("auto").checked=sharing?sharing.auto_approve_members:true;
      $("concurrency").value=(sharing&&sharing.max_concurrent_tasks)||1;
      renderKinds(srv); renderTools(srv);
    } else { renderToolStates() }
    $("usage").textContent=(flash||t("usage",srv.tasks_started_last_24h,g.max_tasks_per_day))+t("cap",g.member_cap);
    renderPresets(srv);
    renderMembers(srv);
    renderTasks(srv);
  }
  const notes=[];
  if(srv&&!srv.live_ready)notes.push(t("live_blocked"));
  if(S.connection_error)notes.push(t("e_"+S.connection_error));
  else if(S.server_error)notes.push(t("e_server_unreachable")+S.server_error);
  if(!S.bundle_ok)notes.push(t("e_bundle_missing"));
  show($("notice"),notes.length>0); $("notice").textContent=notes.join(" ");
}
function tone(p){return p>=30?"":p>=10?"warn":"bad"}
function windowName(m){return m===10080?t("win_week"):m%1440===0?t("win_days",m/1440):m%60===0?t("win_hours",m/60):t("win_minutes",m)}
function resetText(ts){
  if(!ts)return "";
  const min=Math.max(1,Math.round((ts*1000-Date.now())/60000));
  if(min<60)return t("reset_min",min);
  if(min<1440)return t("reset_hm",Math.floor(min/60),min%60);
  const d=new Date(ts*1000), loc=lang==="zh"?"zh-CN":"en-US";
  return t("reset_at",d.toLocaleDateString(loc,{weekday:"short"})+" "+d.toLocaleTimeString(loc,{hour:"2-digit",minute:"2-digit"}));
}
function gauge(title,right,pct,sub){
  const box=el("div","gauge"), top=el("div","top");
  top.append(el("span",null,title),el("small",null,right));box.append(top);
  if(pct!=null){const bar=el("div","bar"),fill=el("i",tone(pct));fill.style.width=Math.max(pct?3:0,Math.min(pct,100))+"%";bar.append(fill);box.append(bar)}
  if(sub)box.append(el("small",null,sub));
  return box;
}
function renderGauges(srv){
  const box=$("gauges"), q=S.quota;
  show(box,!!(srv||q)); box.innerHTML="";
  const wins=(q&&q.windows)||[];
  wins.slice(0,2).forEach(w=>box.append(gauge(windowName(w.minutes),t("left",w.remaining_percent),w.remaining_percent,resetText(w.resets_at))));
  if(q&&!wins.length){const lv=q.level||"ok";const g=gauge(t("quota_title"),"",null,null);
    const s=el("div",null,t("quota_fb_"+lv));s.style.marginTop="6px";if(lv!=="ok")s.style.color=lv==="low"?"var(--warn)":"var(--err)";
    g.append(s);g.style.gridColumn="span 2";box.append(g)}
  if(!srv)return;
  const u=srv.usage||{tasks_started_last_24h:srv.tasks_started_last_24h||0,execution_minutes_last_24h:0,running:0,queued:0};
  const live=(srv.tasks||[]).filter(x=>x.status==="RUNNING"||x.status==="RESERVED"||x.status==="VERIFYING");
  const now=gauge(t("now_title"),"",null,null);
  const chip=el("span","chip"+(u.running?" run":""),u.running?t("now_running",u.running):t("now_idle"));
  now.querySelector("small").replaceWith(chip);
  live.slice(0,2).forEach(x=>{
    const elp=x.started_at?mmss((Date.now()-Date.parse(x.started_at))/1000)+" / "+mmss(x.max_duration_seconds||0):"";
    const pct=x.progress&&x.progress.percent!=null?" · "+x.progress.percent+"%":"";
    const line=el("div",null,kindLabel(x)+(elp?" · "+elp:"")+pct);line.style.marginTop="6px";now.append(line)});
  now.append(el("small",null,t("now_sub",u.queued,u.tasks_started_last_24h,u.execution_minutes_last_24h)));
  box.append(now);
}
const PRESETS={save:{tasks:3,minutes:10,concurrency:1,network:false},standard:{tasks:5,minutes:10,concurrency:1,network:true},
  open:{tasks:10,minutes:30,concurrency:2,network:true}};
function offeredKinds(srv){return (srv.kinds||[]).filter(k=>k.offered&&k.status!=="DISABLED").map(k=>k.kind)}
function currentPreset(srv){
  const g=srv.group, sh=srv.sharing||{}, tools=sh.allowed_tools||TOOL_ORDER, kinds=offeredKinds(srv);
  const allKinds=!sh.allowed_task_types||kinds.every(k=>sh.allowed_task_types.includes(k));
  return Object.keys(PRESETS).find(n=>{const p=PRESETS[n];return allKinds&&g.max_tasks_per_day===p.tasks&&
    Math.round(g.max_task_duration_seconds/60)===p.minutes&&(sh.max_concurrent_tasks||1)===p.concurrency&&
    tools.includes("network")===p.network})||"custom";
}
function renderPresets(srv){
  const cur=currentPreset(srv), box=$("presets");
  if(!box.children.length||box.dataset.lang!==lang){box.innerHTML="";box.dataset.lang=lang;
    ["save","standard","open"].forEach(n=>{const b=el("button",null,t("preset_"+n));b.type="button";b.dataset.preset=n;
      b.onclick=()=>applyPreset(n);box.append(b)})}
  [...box.children].forEach(b=>{b.classList.toggle("on",b.dataset.preset===cur);b.setAttribute("aria-pressed",b.dataset.preset===cur)});
  const g=srv.group, sh=srv.sharing||{}, kinds=offeredKinds(srv);
  const allowed=sh.allowed_task_types?kinds.filter(k=>sh.allowed_task_types.includes(k)).length:kinds.length;
  $("preset_summary").textContent=(cur==="custom"?t("preset_custom")+" · ":"")+t("preset_summary",g.max_tasks_per_day,
    Math.round(g.max_task_duration_seconds/60),sh.max_concurrent_tasks||1,(sh.allowed_tools||TOOL_ORDER).includes("network"),allowed,kinds.length);
}
async function applyPreset(n){
  const srv=S&&S.server; if(!srv)return;
  const p=PRESETS[n], sh=srv.sharing||{}, tools=(sh.allowed_tools||TOOL_ORDER).filter(x=>x!=="network");
  if(p.network&&!(sh.allowed_tools||TOOL_ORDER).includes("network")&&!confirm(t("network_confirm")))return;
  settingsEditing=false;
  await act("settings",{max_members:srv.group.max_members,max_tasks_per_day:p.tasks,max_task_minutes:p.minutes,
    max_concurrent_tasks:p.concurrency,auto_approve_members:sh.auto_approve_members!==false,
    allowed_task_types:offeredKinds(srv),allowed_tools:p.network?[...tools,"network"]:tools});
}
let openEditor=null;
function renderMembers(srv){
  const g=srv.group, mb=$("member_rows");
  $("members_count").textContent=t("members_count",srv.members.length,g.max_members);
  // Do not rebuild the rows while the owner is typing a member's limit.
  if(mb.contains(document.activeElement))return;
  mb.innerHTML="";
  srv.members.forEach(m=>{
    const row=el("div","li"), name=el("span","name");
    name.append(el("span","av",m.is_owner?t("you"):m.user_id.slice(0,2)),el("span",null,m.is_owner?t("owner_name"):t("member_name",m.user_id.slice(0,8))));
    row.append(name);
    if(m.is_owner){row.append(el("small",null,"—"));mb.append(row);return}
    const used=el("small",null,t("m_used_short",m.tasks_last_24h||0,m.minutes_last_24h||0));
    const cap=m.max_tasks_per_day||g.max_tasks_per_day, mini=el("span","mini"), fill=el("i");
    fill.style.width=Math.min(100,100*(m.tasks_last_24h||0)/Math.max(1,cap))+"%";mini.append(fill);used.append(mini);
    if(m.max_tasks_per_day||m.max_minutes_per_day)used.append(" · "+t("m_capped"));
    const btn=el("button","s",t("limits_btn"));btn.type="button";
    btn.onclick=()=>{openEditor=openEditor===m.user_id?null:m.user_id;renderMembers(srv)};
    const right=el("span");right.style.cssText="display:flex;gap:10px;align-items:center";right.append(used,btn);
    row.append(right);mb.append(row);
    if(openEditor!==m.user_id)return;
    const ed=el("div","editor");
    const field=(key,max,label)=>{const l=el("label",null,label);const i=el("input");i.type="number";i.min="1";i.max=String(max);
      i.placeholder=t("m_no_cap");i.value=m[key]??"";l.append(i);ed.append(l);return i};
    const ti=field("max_tasks_per_day",50,t("m_task_cap")), mi=field("max_minutes_per_day",1440,t("m_min_cap"));
    const save=el("button","s p",t("save"));save.type="button";
    save.onclick=()=>{const v=x=>x.value.trim()===""?null:Number(x.value);
      const body={user_id:m.user_id,max_tasks_per_day:v(ti),max_minutes_per_day:v(mi)};
      const bad=[[body.max_tasks_per_day,50],[body.max_minutes_per_day,1440]].some(([x,hi])=>x!==null&&(!Number.isInteger(x)||x<1||x>hi));
      if(bad){localErr={code:"invalid_input",detail:t("m_cap_range")};render();return}
      openEditor=null;act("member-limits",body)};
    const rm=el("button","s",t("remove"));rm.type="button";
    rm.onclick=()=>{if(confirm(t("confirm_remove"))){openEditor=null;act("remove-member",{user_id:m.user_id})}};
    ed.append(save,rm);const help=el("small",null,t("m_caps_help"));help.style.flexBasis="100%";ed.append(help);mb.append(ed)});
}
function taskChip(x){
  if(x.status==="SUCCEEDED")return ["ok",label("s_",x.status)];
  if(x.failure&&x.status!=="CANCELLED")return ["bad",t("fc_"+(x.failure.category||"OTHER"))];
  if(["FAILED","TIMED_OUT","REJECTED"].includes(x.status))return ["bad",label("s_",x.status)];
  if(["RUNNING","RESERVED","VERIFYING","CANCELLING"].includes(x.status))
    return ["run",label("s_",x.status)+(x.progress&&x.progress.percent!=null?" "+x.progress.percent+"%":"")];
  if(x.approval_status==="PENDING")return ["wait",t("pending")];
  if(x.status==="QUEUED")return ["wait",x.queue_position?t("queued_n",x.queue_position):label("s_",x.status)];
  return ["",label("s_",x.status)];
}
function renderTasks(srv){
  const box=$("task_rows"); box.innerHTML="";
  const tasks=srv.tasks||[]; show($("tasks_empty"),!tasks.length);
  const owner=(srv.members||[]).find(m=>m.is_owner);
  tasks.slice(0,8).forEach(x=>{
    const row=el("div","li"), left=el("span");
    const who=owner&&x.requester_id===owner.user_id?t("you"):t("member_name",String(x.requester_id||"").slice(0,8));
    left.append(el("span",null,kindLabel(x)),el("small",null," · "+who+" · "+fmt(x.created_at)+(x.execution_mode==="SIMULATED"?" · "+t("m_SIMULATED"):"")));
    const [cls,text]=taskChip(x);
    row.append(left,el("span","chip "+cls,text));box.append(row)});
}
function inviteMessage(link,needsTailscale){
  const steps=needsTailscale?[t("inv_step_tailscale"),t("inv_step_open",link)]:[t("inv_step_browser",link)];
  return [t("inv_head"),...[...steps,t("inv_step_submit")].map((s,i)=>`${i+1}. ${s}`),t("inv_tail")].join("\n");
}
function renderNetworks(ph){
  const nets=S.networks||[], sel=$("net");
  show($("net_box"),nets.length>1); show($("net_note"),nets.length>1);
  if(ph==="ERROR"&&nets.some(n=>n.reachable===false))$("connection_options").open=true;
  if(nets.length<2)return;
  const mark=n=>n.reachable===true?" · "+t("net_ok"):n.reachable===false?" · "+t("net_down"):"";
  if(document.activeElement!==sel){sel.innerHTML="";
    [["auto",t("net_auto")],...nets.map(n=>[n.id,n.label+mark(n)])].forEach(([v,l])=>{
      const o=document.createElement("option");o.value=v;o.textContent=l;sel.append(o)});
    sel.value=S.network_choice||"auto"}
  sel.disabled=ph==="ONLINE"||ph==="STARTING"||ph==="STOPPING";
  const cur=nets.find(n=>n.id===S.network);
  $("net_note").textContent=(cur?t("net_using",cur.label)+" · ":"")+t("net_hint");
}
const TOOL_ORDER=["git","python-data","doc","code","network"];
function hasTool(srv,x){return x==="code"||x==="doc"||x==="network"||(S.tools&&(x==="git"?S.tools.git:S.tools[x]&&S.tools[x].state==="READY"))}
function renderKinds(srv){
  const box=$("kinds"); box.innerHTML="";
  const allowed=new Set((srv.sharing&&srv.sharing.allowed_task_types)||(srv.kinds||[]).map(k=>k.kind));
  (srv.kinds||[]).filter(k=>k.offered&&k.status!=="DISABLED").forEach(k=>{
    const l=document.createElement("label"); const c=document.createElement("input");
    c.type="checkbox"; c.dataset.kind=k.kind; c.checked=allowed.has(k.kind);
    c.addEventListener("change",()=>settingsEditing=true);
    const name=document.createElement("span"); name.textContent=lang==="zh"?k.name_zh:k.name_en;
    const st=document.createElement("small"); st.textContent=t("ks_"+k.status);
    const need=k.required_tools.filter(x=>x!=="code"&&x!=="doc");
    l.append(c,name,st);
    if(need.length){const n=document.createElement("small");
      n.textContent=t("needs")+need.map(x=>t("tool_"+x).split("（")[0].split(" (")[0]+(hasTool(srv,x)?"":t("missing_tool"))).join("、");l.append(n)}
    box.append(l)});
}
function renderTools(srv){
  const box=$("tools"); box.innerHTML="";
  const allowed=new Set((srv.sharing&&srv.sharing.allowed_tools)||TOOL_ORDER);
  TOOL_ORDER.forEach(tool=>{
    const l=document.createElement("label"); const c=document.createElement("input");
    c.type="checkbox"; c.dataset.tool=tool; c.checked=allowed.has(tool); c.title=t("allow");
    c.addEventListener("change",()=>{settingsEditing=true;
      if(tool==="network"&&c.checked&&!confirm(t("network_confirm")))c.checked=false});
    const name=document.createElement("span"); name.textContent=t("tool_"+tool);
    const st=document.createElement("small"); st.id="tool_state_"+tool;
    l.append(c,name,st);
    if(tool==="python-data"){const b=document.createElement("button");b.className="s";b.type="button";b.id="tool_btn";l.append(b)}
    box.append(l)});
  renderToolStates();
}
function renderToolStates(){
  if(!S||!S.tools)return;
  const n=$("tool_state_network"); if(n)n.textContent=t("network_hint");
  const g=$("tool_state_git"); if(g)g.textContent=S.tools.git?t("installed"):t("not_installed")+" · "+t("git_hint");
  const d=S.tools["python-data"], st=$("tool_state_python-data"), b=$("tool_btn");
  if(!st||!b)return;
  const label={READY:t("installed"),MISSING:t("not_installed"),INSTALLING:t("installing")+" "+(d.detail||""),
    FAILED:t("install_failed")+(d.detail||""),UNAVAILABLE:t("not_installed")}[d.state]||d.state;
  st.textContent=label;
  b.classList.toggle("hidden",d.state==="UNAVAILABLE"||d.state==="INSTALLING");
  b.textContent=d.state==="READY"?t("remove_btn"):t("install_btn",d.size_mb||40);
  b.onclick=()=>act(d.state==="READY"?"remove-tool":"install-tool",{tool:"python-data"});
}
function kindLabel(x){
  const k=S&&S.server&&(S.server.kinds||[]).find(k=>k.kind===x.task_type);
  return k?(lang==="zh"?k.name_zh:k.name_en):label("tt_",x.task_type);
}
function statusText(x){
  let s=label("s_",x.status);
  if(x.status==="QUEUED"&&x.queue_position)s+=" · "+t("queued_n",x.queue_position);
  if(x.failure&&x.status!=="CANCELLED")s+=" · "+t("fc_"+(x.failure.category||"OTHER"))+(x.failure.code?` (${x.failure.code})`:"");
  return s;
}
function mmss(sec){sec=Math.max(0,Math.round(sec));return Math.floor(sec/60)+":"+String(sec%60).padStart(2,"0")}
const PROGRESS_ZH=[[/^Prepared (\d+) attachment/,"已准备 $1 个附件"],[/^Verified local ChatGPT login/,"已确认本机 ChatGPT 登录"],
 [/^Checked out the pinned commit/,"已取出指定版本的代码"],[/^Checking the result/,"正在检查结果"],[/^Result ready/,"结果已就绪"],
 [/^Codex run complete/,"Codex 已完成"],[/^Loaded task metadata \(mock/,"模拟：已读取任务信息"],[/^Generated deterministic fixture/,"模拟：已生成占位结果"]];
function progressText(m){if(!m||lang!=="zh")return m||"";
  for(const [re,zh] of PROGRESS_ZH){const g=m.match(re);if(g)return zh.replace("$1",g[1]||"")}
  return "Codex 正在处理…"}
function runningCount(){return Math.max(S&&S.running_here||0,S&&S.server&&S.server.usage?S.server.usage.running:0)}
async function act(route, body){
  localErr=null;
  let ok=true;
  try{S=await call(route,body)}
  catch(e){ok=false;localErr={code:e.code,detail:e.message,server_code:e.server_code};
    setTimeout(()=>{localErr=null;render()},8000)}
  render();
  return ok;
}
async function poll(){try{S=await call("state");render()}catch(e){}}

$("lang").onclick=()=>{lang=lang==="zh"?"en":"zh";try{localStorage.setItem("sa_lang",lang)}catch(e){}applyStatic();render()};
$("big").onclick=$("go").onclick=()=>{
  if(S&&S.phase==="ONLINE"&&runningCount()>0){
    $("oc_text").textContent=t("oc_text",runningCount()); show($("offline_choice"),true); $("oc_finish").focus(); return}
  act("toggle",{device_label:$("device").value,subscription_type:$("plan").value});
};
function offline(mode){show($("offline_choice"),false);act("toggle",{offline_mode:mode})}
$("oc_finish").onclick=()=>offline("finish");
$("oc_now").onclick=()=>offline("now");
$("oc_cancel").onclick=()=>show($("offline_choice"),false);
$("cancel").onclick=()=>act("cancel");
$("net").onchange=()=>act("network",{network:$("net").value});
$("rotate").onclick=()=>act("invite");
$("copy").onclick=async()=>{try{await navigator.clipboard.writeText(inviteMessage(S.invite_link,!(S.server&&S.server.public_dashboard)));$("copy").textContent=t("copied");
  setTimeout(()=>$("copy").textContent=t("copy"),1500)}catch(e){}};
function checkLimits(v){
  const lo=Math.max(2,S&&S.server?S.server.members.length:2), hi=S&&S.server?S.server.group.member_cap:v.max_members;
  if(![v.max_members,v.max_tasks_per_day,v.max_task_minutes].every(Number.isInteger))return t("r_integer");
  if(v.max_members<lo||v.max_members>hi)return t("r_members",lo,hi);
  if(v.max_tasks_per_day<1||v.max_tasks_per_day>50)return t("r_tasks");
  if(v.max_task_minutes<1||v.max_task_minutes>60)return t("r_minutes");
  if(!Number.isInteger(v.max_concurrent_tasks)||v.max_concurrent_tasks<1||v.max_concurrent_tasks>3)return t("r_concurrency");
  if(v.allowed_task_types&&!v.allowed_task_types.length)return t("r_kinds");
  return "";
}
function setFlash(text,ms){flash=text;render();if(ms)setTimeout(()=>{flash="";render()},ms)}
$("save").onclick=async()=>{
  const v={max_members:Number($("max_members").value),max_tasks_per_day:Number($("max_tasks").value),
    max_task_minutes:Number($("max_minutes").value),auto_approve_members:$("auto").checked,
    max_concurrent_tasks:Number($("concurrency").value)};
  const kinds=[...document.querySelectorAll("#kinds input[data-kind]")];
  if(kinds.length)v.allowed_task_types=kinds.filter(c=>c.checked).map(c=>c.dataset.kind);
  const tools=[...document.querySelectorAll("#tools input[data-tool]")];
  if(tools.length)v.allowed_tools=tools.filter(c=>c.checked).map(c=>c.dataset.tool);
  const bad=checkLimits(v);
  if(bad){setFlash(t("save_failed")+bad+" · ",6000);return}  // keep the user's edits on screen
  $("save").disabled=true;setFlash(t("saving")+" · ");
  const ok=await act("settings",v);
  $("save").disabled=false;
  if(ok){settingsEditing=false;setFlash(t("saved")+" · ",2000)}
  else{setFlash(t("save_failed").replace(/[：:\s]+$/,"")+" · ",8000)}  // reason is in the error box
};
$("dashboard").onclick=async()=>{try{const r=await call("dashboard");window.open(r.url,"_blank","noopener")}catch(e){localErr={code:e.code,detail:e.message};render()}};
["max_members","max_tasks","max_minutes","concurrency","auto"].forEach(id=>$(id).addEventListener("input",()=>settingsEditing=true));
["device","plan"].forEach(id=>$(id).addEventListener("input",()=>onboardingEditing=true));
applyStatic(); poll(); setInterval(poll,1500);
</script>
</body>
</html>
"""
