"""Local-only presentation assets shared by the login and conversation pages."""

BRAND = '''<div class="cp-brand"><span class="cp-mark" aria-hidden="true">
<svg viewBox="0 0 32 32" fill="none"><path d="M6 8h20v13H15l-6 5v-5H6V8Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M11 13h10M11 17h6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
</span><div><span class="cp-wordmark">CampusPilot</span><span class="cp-brand-note">新生入学助手</span></div></div>'''

WELCOME = '''<div class="cp-welcome"><h1>入学的事，从这里问起。</h1>
<p>查一条规定，理一份清单，或记下一件待办。</p></div>'''

FEATURES = '''<aside class="cp-features" aria-label="功能介绍"><h2>还可以这样用</h2>
<dl><dt>整理办理清单</dt><dd>把要做的事理清楚，确认后再保存。</dd>
<dt>管理个人待办</dt><dd>记录截止日期，随时查看和标记完成。</dd>
<dt>个人日程与导出</dt><dd>指定起止时间后创建日程，支持日历文件导出；不主动推送通知。</dd></dl>
<p class="cp-side-note">直接在对话里说就好。</p></aside>'''

CSS = '''
:root { color-scheme: light; }
body, .gradio-container { background: #fff !important; color: #182b49 !important;
  font-family: "Segoe UI", "Microsoft YaHei UI", "PingFang SC", sans-serif !important; }
.gradio-container { width: 100% !important; max-width: 1200px !important; margin: 0 auto !important; padding: 0 clamp(18px, 3vw, 36px) 28px !important;
  --body-background-fill: #fff; --background-fill-primary: #fff; --background-fill-secondary: #f7f9fc;
  --body-text-color: #182b49; --body-text-color-subdued: #65748a; --border-color-primary: #e3e9f0;
  --block-background-fill: #fff; --block-border-color: #e3e9f0; --block-shadow: none;
  --input-background-fill: #fff; --input-border-color: #e3e9f0; --input-shadow: none;
  --button-primary-background-fill: #25466e; --button-primary-background-fill-hover: #1c3657;
  --button-primary-text-color: #fff; --button-secondary-background-fill: #fff;
  --button-secondary-text-color: #25466e; --button-secondary-border-color: #e3e9f0; }
footer { display: none !important; }
.gradio-container > main { padding: 0 !important; }
#cp-header { align-items: center; border-bottom: 1px solid #e3e9f0; padding: 22px 0 !important; gap: 16px; }
#cp-header .cp-header-brand { flex: 1 !important; min-width: 200px; }
#cp-header .html-container { padding: 0 !important; }
.cp-brand { display: flex; align-items: center; gap: 12px; }
.cp-mark { width: 38px; height: 38px; color: #25466e; display: inline-flex; }
.cp-mark svg { width: 100%; height: 100%; }
.cp-wordmark { display: block; color: #182b49; font-size: 21px; font-weight: 650; letter-spacing: -.6px; line-height: 1.25; }
.cp-brand-note { display: block; font-size: 12px; color: #65748a; margin-top: 4px; }
#cp-new { flex: 0 0 auto !important; width: 90px; min-width: 90px !important; max-width: 100px; border: 1px solid #d5dfe9; border-radius: 7px; min-height: 36px; box-shadow: none; }
#cp-logout { flex: 0 0 auto !important; min-width: 60px; width: 60px; max-width: 60px; }
#cp-logout form { margin: 0; }
#cp-logout button { color: #65748a; background: none; border: 0; cursor: pointer; font: inherit; font-size: 13px; padding: 8px; white-space: nowrap; }
#cp-layout { gap: 0 !important; align-items: stretch; }
#cp-main { padding: 12px 38px 0 0 !important; min-width: 0 !important; }
#cp-sidebar { border-left: 1px solid #e3e9f0; padding: 42px 0 0 32px !important; min-width: 0 !important; }
#cp-chat { border: 0 !important; background: #fff !important; border-radius: 0 !important; height: clamp(300px, 49vh, 500px) !important; }
#cp-chat .bubble-wrap { padding: 12px 4px !important; }
#cp-chat .message { font-size: 15px; line-height: 1.85; }
#cp-chat .message.user { background: #f0f4f8 !important; border: 0 !important; }
#cp-chat .message.bot { background: #fff !important; border: 0 !important; }
.cp-welcome { text-align: left; width: 100%; padding: 0 28px; margin-top: -10px; }
.cp-welcome h1 { color: #182b49; font-size: clamp(24px, 2.6vw, 32px) !important; font-weight: 550; letter-spacing: -1px; margin: 0 0 15px !important; }
.cp-welcome p { color: #65748a; font-size: 14px !important; margin: 0 !important; line-height: 1.8; }
#cp-composer { border: 1px solid #ced9e5; border-radius: 12px; padding: 12px !important; gap: 12px !important; align-items: flex-end; flex-wrap: nowrap !important; background: #fff; }
#cp-message { min-width: 0 !important; }
#cp-message, #cp-message label, #cp-message .input-container { border: 0 !important; padding: 0 !important; background: transparent !important; box-shadow: none !important; }
#cp-message textarea { border: 0 !important; border-radius: 0 !important; background: transparent !important; box-shadow: none !important; min-height: 52px; padding: 4px 2px !important; font-size: 15px !important; line-height: 1.65; resize: none; }
#cp-message textarea:focus { outline: none; }
#cp-composer:focus-within { border-color: #25466e; box-shadow: 0 0 0 2px #e8eef5; }
#cp-send { flex: 0 0 68px !important; min-width: 68px !important; width: 68px; max-width: 68px; min-height: 39px; border-radius: 8px; box-shadow: none; font-size: 14px; }
#cp-hint { font-size: 11px; color: #748197; margin: -2px 2px 4px; }
#cp-hint p { font-size: 11px; color: #748197; }
#cp-examples { margin: 2px 0 14px !important; }
#cp-examples .label { display: none !important; }
#cp-examples, #cp-examples .styler, #cp-examples .gallery { background: transparent !important; }
#cp-examples .gallery { gap: 8px !important; }
#cp-examples .gallery-item { border: 1px solid #e3e9f0 !important; border-radius: 6px !important; background: #fff !important; color: #65748a !important; padding: 7px 10px !important; font-size: 12px !important; }
#cp-examples .gallery-item:hover { border-color: #25466e !important; }
#cp-examples table { border: 0 !important; }
#cp-examples td { color: #5b6e86; font-size: 12px; background: #fff; border-color: #e3e9f0 !important; padding: 8px 12px !important; }
#cp-confirm { border: 1px solid #ccd9e8; border-left: 3px solid #25466e; border-radius: 8px; padding: 18px !important; margin: 12px 0; background: #f9fbfe; }
#cp-confirm .prose { max-height: 360px; overflow-y: auto; }
#cp-confirm button { min-height: 38px; border-radius: 7px; }
.cp-fold { border: 0 !important; border-top: 1px solid #e3e9f0 !important; border-radius: 0 !important; background: #fff !important; box-shadow: none !important; }
.cp-fold > button { padding: 15px 0 !important; background: #fff !important; font-size: 13px !important; color: #65748a !important; font-weight: 400 !important; }
.cp-fold .prose { font-size: 13px; }
.cp-features h2 { margin: 0 0 29px; font-size: 16px; font-weight: 600; color: #182b49; }
.cp-features dl { margin: 0; }
.cp-features dt { font-size: 14px; color: #25466e; font-weight: 550; margin: 24px 0 7px; }
.cp-features dd { font-size: 13px; line-height: 1.85; color: #65748a; margin: 0; }
.cp-side-note { color: #65748a; font-size: 12px; margin: 24px 0 26px; }
#cp-scope { margin-top: 30px; padding-top: 22px; border-top: 1px solid #e3e9f0; }
#cp-scope p { color: #748197; font-size: 11px !important; line-height: 1.9; }
#cp-sidebar .cp-fold { margin-top: 2px; }
button:focus-visible, a:focus-visible, input:focus-visible, textarea:focus-visible { outline: 2px solid #25466e !important; outline-offset: 3px; }
@media (max-width: 760px) {
  #cp-header { padding: 18px 0 !important; gap: 8px; flex-wrap: nowrap; }
  #cp-header .cp-header-brand { min-width: 130px !important; }
  .cp-wordmark { font-size: 18px; }
  .cp-brand { gap: 7px; } .cp-mark { width: 30px; height: 30px; }
  #cp-layout { flex-direction: column; }
  #cp-main { padding: 12px 0 0 !important; }
  #cp-sidebar { border-left: 0; border-top: 1px solid #e3e9f0; padding: 30px 0 0 !important; margin-top: 20px; }
  #cp-chat { height: clamp(260px, 40vh, 350px) !important; }
  .cp-welcome { padding: 0 6px; }
  .cp-welcome h1 { font-size: 24px !important; letter-spacing: -.6px; }
  .cp-welcome p { font-size: 12px !important; }
  #cp-new { width: 70px; min-width: 70px !important; }
  #cp-logout { width: 46px; min-width: 46px; max-width: 46px; }
  #cp-logout button { padding: 8px 4px; }
}
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation: none !important; transition: none !important; } }
'''

LOGIN_HTML = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>CampusPilot · 登录</title>
<style>
*{box-sizing:border-box}body{margin:0;color:#182b49;background:#fff;font:15px/1.7 "Segoe UI","Microsoft YaHei UI",sans-serif}
header{max-width:1120px;margin:0 auto;padding:28px 32px;border-bottom:1px solid #e3e9f0;display:flex;align-items:center;justify-content:space-between}
.cp-brand{display:flex;gap:12px;align-items:center}.cp-mark{display:flex;width:38px;height:38px;color:#25466e}.cp-mark svg{width:100%}
.cp-wordmark{font-size:21px;font-weight:650;letter-spacing:-.6px;display:block;line-height:1.3}.cp-brand-note{display:block;font-size:12px;color:#65748a;margin-top:3px}
.invite{font-size:12px;color:#65748a}main{max-width:900px;padding:0 32px;margin:clamp(50px,13vh,130px) auto;display:grid;grid-template-columns:1fr 330px;gap:90px;align-items:start}
.intro{padding-top:12px}.intro h1{font-size:34px;line-height:1.6;font-weight:550;letter-spacing:-1px;margin:0 0 18px}.intro p{color:#65748a;font-size:14px;max-width:310px}
.features{margin-top:38px;display:flex;gap:22px;color:#25466e;font-size:13px}.login h2{font-size:21px;font-weight:550;margin:0 0 8px}.login>p{font-size:13px;color:#65748a;margin:0 0 28px}
label{display:block;font-size:13px;margin:18px 0 6px}input{display:block;width:100%;padding:12px 13px;font:inherit;background:#fff;border:1px solid #d5dfe9;border-radius:7px;color:#182b49;outline:none}
input:focus{border-color:#25466e;box-shadow:0 0 0 2px #e8eef5}button{width:100%;font:inherit;font-size:14px;color:#fff;background:#25466e;border:0;border-radius:7px;padding:12px;cursor:pointer;margin-top:26px}button:hover{background:#1c3657}
button:focus-visible{outline:2px solid #25466e;outline-offset:3px}.notice{font-size:11px;color:#748197;line-height:1.9;margin-top:20px}.foot{max-width:1056px;margin:60px auto 24px;padding:0 32px;color:#748197;font-size:11px}
@media(max-width:680px){header{padding:20px}main{display:block;margin:42px auto;max-width:420px;padding:0 26px}.intro{padding:0;margin-bottom:36px}.intro h1{font-size:26px;margin-bottom:10px}.intro p{font-size:12px}.features{display:none}.foot{padding:0 26px;margin-top:40px}}
</style></head><body><header>''' + BRAND + '''<span class="invite">邀请体验</span></header>
<main><section class="intro"><h1>入学的事，<br>从这里问起。</h1><p>查一条规定，理一份清单，<br>把要办的事放在心里，也记在这里。</p><div class="features"><span>有来源的回答</span><span>确认后才保存</span></div></section>
<section class="login" aria-label="登录"><h2>欢迎回来</h2><p>使用组织者分配的账号继续。</p>
<form method="post" action="/login"><label for="username">账号</label><input id="username" name="username" autocomplete="username" required maxlength="40" placeholder="输入邀请账号">
<label for="password">密码</label><input id="password" name="password" type="password" autocomplete="current-password" required maxlength="256" placeholder="输入密码"><button type="submit">进入 CampusPilot</button></form>
<p class="notice">个人会话与事项按账号隔离。<br>请勿提交身份证、录取通知书等敏感材料。</p></section></main>
<p class="foot">独立项目，非校方系统。办理安排以学校最新通知为准。</p></body></html>'''
