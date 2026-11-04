"use strict";
// 勤務表エンジン サンプルのフロント。エンジンとの受け渡しは CSV だけ（サーバーが CSV の読み書きと実行を仲介する）。

const $ = (sel, el = document) => el.querySelector(sel);
const h = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "class") e.className = v;
    else if (v === true) e.setAttribute(k, "");
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const kid of kids.flat()) e.append(kid instanceof Node ? kid : document.createTextNode(kid ?? ""));
  return e;
};
const api = async (method, url, body) => {
  const r = await fetch(url, {method, headers: {"Content-Type": "application/json"}, body: body ? JSON.stringify(body) : undefined});
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
};
const toast = (msg, ms = 2500) => { const t = $("#toast"); t.textContent = msg; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => t.hidden = true, ms); };

// ---------------------------------------------------------------- 状態
const FILES = ["shifts.csv", "nurses.csv", "history.csv", "demand.csv", "fixed.csv", "requests.csv", "params.csv", "constraints.csv"];
const tables = {};            // name -> {rows: [[見出し], [..], ..]}
const dirty = new Set();
const WEEKDAYS = [...("月火水木金土日")];
const CATS = {D: "日勤帯", E: "準夜帯", N: "深夜帯", P: "パート", OFF: "公休", LV: "有休等"};
const col = (name, head) => tables[name].rows[0].indexOf(head);
const body = name => tables[name].rows.slice(1);
const shiftNames = () => body("shifts.csv").map(r => r[0]).filter(Boolean);
const nurseNames = () => body("nurses.csv").map(r => r[0]).filter(Boolean);

// 画面ごとの列の定義（type: text / number / time / date / bool / select / multi）
const SCHEMA = {
  "shifts.csv": {title: "勤務", help: "勤務の種類と時間。時刻は30分単位の HH:MM。翌日にかかる終了は 25:30 のように24時を超えて書く。区分: D=日勤帯 E=準夜帯 N=深夜帯 P=パート OFF=公休 LV=有休等。「休」は必須。固定専用=確定休・有休のように、固定割当にだけ使える勤務。",
    cols: {"勤務名": {}, "開始": {type: "time"}, "終了": {type: "time"}, "区分": {type: "text", list: Object.keys(CATS)}, "勤務扱い": {type: "bool"}, "固定専用": {type: "bool"}}},
  "nurses.csv": {title: "看護師", help: "取れる勤務は、その人が就ける勤務（「休」を含める）。夜勤リーダー資格者は、夜勤の各時間帯に1人以上必要です。氏名を変えると、他の表の氏名も書き換えます。",
    cols: {"氏名": {}, "取れる勤務": {type: "multi", opts: shiftNames}, "公休数": {type: "number"}, "夜勤リーダー資格": {type: "bool"}, "夜勤専従": {type: "bool"}, "短時間正職員": {type: "bool"}, "算入閾値(時間)": {type: "number", step: 0.5}}},
  "history.csv": {title: "前月末の履歴", help: "計画の直前の勤務（連続勤務・勤務間隔の判定に使う）。列は前月末の日ごとの勤務。",
    cols: {"氏名": {type: "select", opts: nurseNames}, "*": {type: "select", opts: shiftNames}}},
  "demand.csv": {title: "需要（日ごと）", help: "対象月の全日について、各日を 00:00〜24:00 まで空きなく覆うように、時間帯ごとの必要人数を書く。需要なしの時間帯は必要人数 0。上のバーで、ある日の需要を他の日へコピーできる。",
    cols: {"日付": {type: "date"}, "開始": {type: "time"}, "終了": {type: "time"}, "必要人数": {type: "number"}}},
  "fixed.csv": {title: "固定割当（確定休・有休）", help: "この日はこの勤務に固定する。有休の取得日は、ここに入力した日だけを受け取ります（日数の管理はこのシステムの対象外）。",
    cols: {"氏名": {type: "select", opts: nurseNames}, "日付": {type: "date"}, "勤務": {type: "select", opts: shiftNames}}},
  "requests.csv": {title: "勤務者からの要望", help: "休み＝希望休（その日は休み）。シフト＝その日にその勤務。違反は params の req_viol_ratio の割合まで許容します（全部は守れないことがあります）。",
    cols: {"氏名": {type: "select", opts: nurseNames}, "日付": {type: "date"}, "種別": {type: "select", opts: () => ["休み", "シフト"]}, "勤務": {type: "select", opts: () => ["", ...shiftNames()]}}},
};
const DATA_TABS = ["shifts.csv", "nurses.csv", "history.csv", "demand.csv", "fixed.csv", "requests.csv"];
let currentData = "nurses.csv";

// ---------------------------------------------------------------- 読み込み・保存
async function loadAll() {
  for (const n of FILES) tables[n] = await api("GET", `/api/input/${n}`);
  dirty.clear();
  renderDirty(); renderData(); renderParams();
}
function markDirty(name) { dirty.add(name); renderDirty(); }
function renderDirty() { $("#dirty").textContent = dirty.size ? `未保存: ${[...dirty].join(", ")}` : "保存済み"; $("#save").disabled = !dirty.size; }
async function saveAll() {
  for (const n of [...dirty]) { await api("PUT", `/api/input/${n}`, {rows: tables[n].rows}); dirty.delete(n); }
  renderDirty();
}

// ---------------------------------------------------------------- 表の編集
function cellInput(name, ri, ci, spec, value) {
  const set = v => {
    const old = tables[name].rows[ri][ci];
    tables[name].rows[ri][ci] = v;
    markDirty(name);
    if (name === "nurses.csv" && ci === 0 && old && old !== v) renameNurse(old, v);
  };
  const type = spec.type || "text";
  if (type === "bool") return h("input", {type: "checkbox", checked: value === "1", onchange: e => set(e.target.checked ? "1" : "0")});
  if (type === "select") {
    const opts = spec.opts(); if (value && !opts.includes(value)) opts.push(value);
    return h("select", {onchange: e => set(e.target.value)}, opts.map(o => h("option", {value: o, selected: o === value}, o)));
  }
  if (type === "multi") {
    const cur = new Set(value.split("|").filter(Boolean)), all = spec.opts();
    const sum = h("summary", {}, value || "（なし）");
    const box = h("div", {class: "pop"}, all.map(o => h("label", {}, h("input", {type: "checkbox", checked: cur.has(o), onchange: e => {
      e.target.checked ? cur.add(o) : cur.delete(o);
      const v = all.filter(x => cur.has(x)).join("|"); set(v); sum.textContent = v || "（なし）";
    }}), " " + o)));
    return h("details", {class: "multi"}, sum, box);
  }
  const attrs = {type: type === "number" ? "number" : "text", value, onchange: e => set(e.target.value)};
  if (type === "number") Object.assign(attrs, {step: spec.step ?? 1});
  if (type === "time") Object.assign(attrs, {placeholder: "HH:MM", pattern: "\\d{1,2}:(00|30)", size: 6});
  if (type === "date") Object.assign(attrs, {placeholder: "YYYY-MM-DD", pattern: "\\d{4}-\\d{2}-\\d{2}", size: 11});
  if (spec.list) { attrs.list = "dl-" + name; }
  const inp = h("input", attrs);
  return spec.list ? h("span", {}, inp, h("datalist", {id: "dl-" + name}, spec.list.map(o => h("option", {value: o})))) : inp;
}
function renderTable(name, host) {
  const sch = SCHEMA[name], rows = tables[name].rows;
  const head = rows[0];
  const spec = hd => sch.cols[hd] || sch.cols["*"] || {};
  const di = head.indexOf("日付");                              // 日付の列があれば、曜日を添える（読み取り専用）
  const tbl = h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "#"), head.map(x => h("th", {}, x)), di >= 0 ? h("th", {}, "曜") : null, h("th", {}))),
    h("tbody", {}, rows.slice(1).map((r, i) => h("tr", {class: name === "demand.csv" && r[0] !== rows[i][0] ? "newday" : ""}, h("td", {class: "muted"}, i + 1),
      head.map((hd, ci) => h("td", {}, cellInput(name, i + 1, ci, spec(hd), r[ci] ?? ""))),
      di >= 0 ? h("td", {class: "muted wd"}, weekdayName(r[di])) : null,
      h("td", {}, h("button", {class: "rowbtn", title: "この行を削除", onclick: () => deleteRow(name, i + 1)}, "✕"))))));
  if (di >= 0) tbl.addEventListener("change", () => tbl.querySelectorAll("tbody tr").forEach((tr, i) => { $(".wd", tr).textContent = weekdayName(tables[name].rows[i + 1][di]); }));
  const tools = [h("button", {onclick: () => addRow(name)}, "＋ 行を追加"), ` ${rows.length - 1} 行　`];
  if (name === "demand.csv") tools.push(demandTool());
  host.replaceChildren(h("p", {}, ...tools), h("div", {class: "scroll"}, tbl));
}
function addRow(name) {
  const rows = tables[name].rows, head = rows[0];
  const blank = head.map(hd => ({"勤務扱い": "0", "固定専用": "0", "夜勤リーダー資格": "0", "夜勤専従": "0", "短時間正職員": "0", "公休数": "9", "必要人数": "0", "曜日": "月", "種別": "休み"}[hd] ?? ""));
  if (name === "nurses.csv") {
    blank[0] = `看護師${String(nurseNames().length + 1).padStart(2, "0")}`; blank[col(name, "取れる勤務")] = shiftNames().join("|");
    tables["history.csv"].rows.push([blank[0], ...tables["history.csv"].rows[0].slice(1).map(() => "休")]); markDirty("history.csv");   // 履歴の行も足す
  }
  if (name === "history.csv") for (let i = 1; i < head.length; i++) blank[i] = "休";
  rows.push(blank); markDirty(name); renderData();
}
function deleteRow(name, ri) {
  const gone = tables[name].rows.splice(ri, 1)[0];
  if (name === "nurses.csv") for (const t of ["history.csv", "fixed.csv", "requests.csv"]) {         // その人の行も消す
    const rows = tables[t].rows; tables[t].rows = rows.filter((r, i) => i === 0 || r[0] !== gone[0]); markDirty(t);
  }
  markDirty(name); renderData();
}
function renameNurse(old, nu) {
  for (const t of ["history.csv", "fixed.csv", "requests.csv"]) { let ch = false;
    for (const r of tables[t].rows.slice(1)) if (r[0] === old) { r[0] = nu; ch = true; }
    if (ch) markDirty(t); }
}
function renderData() {
  const nav = $("#subtabs");
  nav.replaceChildren(...DATA_TABS.map(n => h("button", {class: n === currentData ? "on" : "", onclick: () => { currentData = n; renderData(); }}, SCHEMA[n].title)));
  $("#datahelp").textContent = SCHEMA[currentData].help;
  renderTable(currentData, $("#datatable"));
}

// ---------------------------------------------------------------- 月の扱い
const wdOf = d => /^\d{4}-\d{2}-\d{2}$/.test(d) ? new Date(d + "T00:00:00Z").getUTCDay() : -1;      // 0=日
const weekdayName = d => wdOf(d) < 0 ? "" : "日月火水木金土"[wdOf(d)];
const daysIn = ym => { const [y, m] = ym.split("-").map(Number); return new Date(Date.UTC(y, m, 0)).getUTCDate(); };
const isMonth = v => /^\d{4}-(0[1-9]|1[0-2])$/.test(v);
const param = key => (body("params.csv").find(r => r[0] === key) || [])[1] || "";
const periodText = ym => isMonth(ym) ? `${ym}-01 〜 ${ym}-${String(daysIn(ym)).padStart(2, "0")}（${daysIn(ym)}日間）` : "YYYY-MM で入力してください";

// 需要: ある日の需要を、同じ曜日・平日・土日・全日へコピーする
function demandTool() {
  const dates = [...new Set(body("demand.csv").map(r => r[0]))];
  const day = h("select", {id: "dm-day"}, dates.map(d => h("option", {value: d}, `${d.slice(5)}（${weekdayName(d)}）`)));
  const scope = h("select", {id: "dm-scope"}, [["wd", "同じ曜日の全日へ"], ["weekday", "平日（月〜金）へ"], ["weekend", "土日へ"], ["all", "全日へ"]].map(([v, t]) => h("option", {value: v}, t)));
  return h("span", {class: "tool"}, "この日の需要を ", day, scope, h("button", {onclick: () => {
    const src = body("demand.csv").filter(r => r[0] === day.value), sw = wdOf(day.value);
    const hit = d => ({wd: wdOf(d) === sw, weekday: [1, 2, 3, 4, 5].includes(wdOf(d)), weekend: [0, 6].includes(wdOf(d)), all: true}[scope.value]);
    const rows = [tables["demand.csv"].rows[0]];
    for (const d of dates) (hit(d) ? src.map(r => [d, r[1], r[2], r[3]]) : body("demand.csv").filter(r => r[0] === d)).forEach(r => rows.push(r));
    tables["demand.csv"].rows = rows; markDirty("demand.csv"); renderData(); toast("需要をコピーしました");
  }}, "コピー"));
}

// 対象月を変えたとき、日付を持つ表を新しい月へ移す（需要は曜日ごとの標準から、固定割当・要望は同じ「日」へ）
function changeMonth(oldYm, newYm) {
  if (!isMonth(newYm) || !isMonth(oldYm) || oldYm === newYm) return "";
  const tpl = {};
  for (const r of body("demand.csv")) { const w = wdOf(r[0]); if (w < 0) continue; const first = body("demand.csv").find(x => wdOf(x[0]) === w)[0]; if (r[0] === first) (tpl[w] ||= []).push(r); }
  const any = Object.values(tpl)[0] || [];
  const dem = [tables["demand.csv"].rows[0]];
  for (let d = 1; d <= daysIn(newYm); d++) { const date = `${newYm}-${String(d).padStart(2, "0")}`; for (const r of tpl[wdOf(date)] || any) dem.push([date, r[1], r[2], r[3]]); }
  tables["demand.csv"].rows = dem; markDirty("demand.csv");
  let dropped = 0;
  for (const t of ["fixed.csv", "requests.csv"]) {
    const rows = tables[t].rows, out = [rows[0]], di = rows[0].indexOf("日付");
    for (const r of rows.slice(1)) { const dd = Number(r[di].slice(8, 10)); if (dd >= 1 && dd <= daysIn(newYm)) { r[di] = `${newYm}-${String(dd).padStart(2, "0")}`; out.push(r); } else dropped++; }
    tables[t].rows = out; markDirty(t);
  }
  return `対象月を ${newYm} に変更しました。需要は旧月の最初の各曜日を標準に作り直し、固定割当・要望は同じ日へ移しました${dropped ? `（月末を超える ${dropped} 件は除外）` : ""}。`;
}

// ---------------------------------------------------------------- 確認ダイアログ（ブラウザ標準の confirm は使わず、画面内で OK / キャンセルを取る）
function ask(title, nodes, okLabel = "OK") {
  return new Promise(resolve => {
    const dlg = h("dialog", {id: "ask"}, h("h3", {}, title), h("div", {class: "askbody"}, nodes),
      h("div", {class: "askbtns"}, h("button", {id: "ask-cancel", onclick: () => dlg.close("cancel")}, "キャンセル"), h("button", {id: "ask-ok", class: "primary", onclick: () => dlg.close("ok")}, okLabel)));
    dlg.addEventListener("close", () => { const ok = dlg.returnValue === "ok"; dlg.remove(); resolve(ok); });
    document.body.append(dlg); dlg.showModal(); $("#ask-ok").focus();
  });
}

// ---------------------------------------------------------------- パラメータ・制約
function renderParams() {
  const rows = body("params.csv"), groups = {};
  rows.forEach((r, i) => (groups[r[2]] ||= []).push(i + 1));
  const sel = {holiday_rule: ["weekly", "4w4d"], admission_class: ["7:1", "10:1", "other"]};
  const flags = new Set(["holiday_strict_calendar"]);
  $("#params").replaceChildren(...Object.entries(groups).map(([g, idxs]) => h("div", {class: "pg"}, h("h3", {}, g),
    h("table", {}, h("tbody", {}, idxs.map(ri => { const r = tables["params.csv"].rows[ri], key = r[0];
      const set = v => { r[1] = v; markDirty("params.csv"); };
      const period = h("div", {class: "muted"}, key === "target_month" ? periodText(r[1]) : "");
      const input = key === "target_month" ? h("input", {type: "month", id: "target-month", value: r[1], onchange: e => {
          const nu = e.target.value, old = r[1];
          if (!isMonth(nu)) { e.target.value = old; return; }
          r[1] = nu; markDirty("params.csv"); period.textContent = periodText(nu);
          const msg = changeMonth(old, nu); renderData(); if (msg) toast(msg, 7000);
        }})
        : sel[key] ? h("select", {onchange: e => set(e.target.value)}, sel[key].map(o => h("option", {selected: o === r[1]}, o)))
        : flags.has(key) ? h("input", {type: "checkbox", checked: r[1] === "1", onchange: e => set(e.target.checked ? "1" : "0")})
        : h("input", {type: "text", value: r[1], size: 12, onchange: e => set(e.target.value)});
      return h("tr", {}, h("td", {}, h("code", {}, key)), h("td", {}, input), h("td", {class: "muted"}, r[3], period.textContent ? h("div", {}, period) : null));
    }))))));
  const cons = tables["constraints.csv"].rows;
  $("#constraints").replaceChildren(h("table", {}, h("thead", {}, h("tr", {}, ["有効", "制約名", "制約クラス", "層", "根拠"].map(x => h("th", {}, x)))),
    h("tbody", {}, cons.slice(1).map(r => h("tr", {}, h("td", {}, h("input", {type: "checkbox", checked: r[4] === "1", onchange: e => { r[4] = e.target.checked ? "1" : "0"; markDirty("constraints.csv"); }})),
      h("td", {}, h("code", {}, r[0])), h("td", {}, r[2]), h("td", {}, r[1]), h("td", {class: "muted"}, r[3]))))));
}

// ---------------------------------------------------------------- 実行
let polling = null;
async function runEngine() {
  const ym = param("target_month"), off = body("constraints.csv").filter(r => r[4] !== "1").length;
  const ok = await ask("エンジンを実行しますか？", [
    h("p", {}, `対象月: ${ym}（${periodText(ym).split("（")[0]}）`),
    h("p", {}, `看護師 ${nurseNames().length} 人 ・ 外せる制約 ${body("constraints.csv").length - off} 件が有効（${off} 件をオフ）`),
    h("p", {class: "muted"}, `${dirty.size ? "未保存の変更を保存してから、" : ""}求解します。解けないときは矛盾の原因の診断まで行うため、数十秒かかることがあります。`)], "実行");
  if (!ok) return;
  const btn = $("#runbtn"); btn.disabled = true;
  try {
    await saveAll();
    const {id} = await api("POST", "/api/run");
    switchTab("run"); selected = id; await renderHistory();
    const t0 = Date.now();
    clearInterval(polling);
    polling = setInterval(async () => {
      const info = await api("GET", `/api/runs/${id}`);
      if (info.state === "done") { clearInterval(polling); btn.disabled = false; await renderHistory(); await showRun(id); }
      else $("#result").replaceChildren(banner({status: "running", label: `実行中… ${Math.round((Date.now() - t0) / 1000)} 秒`}, "求解（解けなければ矛盾の原因の診断まで）しています。"));
    }, 1000);
  } catch (e) { btn.disabled = false; toast("実行できません: " + e.message, 5000); }
}
let selected = null;
const STATUS_LABEL = {feasible: "実行可能解あり", conflict: "実行不可能（制約矛盾）", timeout: "時間切れ", error: "入力・環境のエラー", running: "実行中", "": "不明"};
async function renderHistory() {
  const runs = await api("GET", "/api/runs");
  $("#history").replaceChildren(...(runs.length ? runs.map(r => h("button", {class: "hist" + (r.id === selected ? " on" : ""), onclick: () => showRun(r.id)},
    h("div", {}, fmtRun(r.id)), h("span", {class: `badge b-${r.state === "running" ? "running" : r.status}`}, r.state === "running" ? "実行中" : STATUS_LABEL[r.status] || r.status),
    h("div", {class: "muted"}, r.n_nurses ? `${r.n_nurses}人 × ${r.n_days}日 ・ ${r.seconds ?? ""}秒` : "")))
    : [h("p", {class: "muted"}, "まだ実行していません")]));
}
const fmtRun = id => `${id.slice(0, 4)}-${id.slice(4, 6)}-${id.slice(6, 8)} ${id.slice(9, 11)}:${id.slice(11, 13)}:${id.slice(13, 15)}`;
const banner = (st, text) => h("div", {class: `banner ${st.status}`}, h("b", {}, STATUS_LABEL[st.status] || st.status), "　", st.label || "", text ? h("div", {}, text) : null);

async function showRun(id) {
  selected = id; await renderHistory();
  const get = (d, n) => api("GET", `/api/runs/${id}/${d}/${n}`).then(t => t.rows);
  const [status, schedule, conflicts, precheck, shifts, fixed, reqs] = await Promise.all(
    [["output", "status.csv"], ["output", "schedule.csv"], ["output", "conflicts.csv"], ["output", "precheck.csv"], ["input", "shifts.csv"], ["input", "fixed.csv"], ["input", "requests.csv"]].map(([d, n]) => get(d, n)));
  const st = Object.fromEntries(status.slice(1).map(r => [r[0], r[1]]));
  if (!st.status) { $("#result").replaceChildren(banner({status: "error", label: "結果がありません（エンジンが異常終了した可能性）"}), h("button", {onclick: () => showLog(id)}, "実行ログを見る")); return; }
  const out = [banner(st, `所要 ${st.seconds} 秒 ・ 看護師 ${st.n_nurses} 人 ・ ${st.n_days} 日 ・ 開始日 ${st.start_date || ""}`)];
  if (st.status === "feasible") out.push(...scheduleView(schedule, shifts, fixed, reqs));
  else if (st.status === "conflict") out.push(conflictView(conflicts, st));
  else out.push(h("pre", {}, st.message));
  if (precheck.length > 1) out.push(h("details", {}, h("summary", {}, `事前検査の指摘（${precheck.length - 1} 件）: 需要と供給の単純な不足`), h("pre", {}, precheck.slice(1).map(r => r[0]).join("\n"))));
  out.push(h("details", {}, h("summary", {}, "診断メッセージの全文・実行ログ"), h("pre", {}, st.message), h("button", {onclick: e => showLog(id, e.target)}, "エンジンの実行ログを表示")));
  $("#result").replaceChildren(...out);
}
async function showLog(id, btn) {
  const {text} = await api("GET", `/api/runs/${id}/log`);
  const pre = h("pre", {}, text || "（ログなし）"); (btn ? btn.parentElement : $("#result")).append(pre);
}

// ---------------------------------------------------------------- 勤務表
function scheduleView(sched, shifts, fixed, reqs) {
  const cat = Object.fromEntries(shifts.slice(1).map(r => [r[0], r[3]]));
  const work = Object.fromEntries(shifts.slice(1).map(r => [r[0], r[4] === "1"]));
  const days = sched[0].slice(1);
  const fx = new Set(fixed.slice(1).map(r => `${r[0]}|${r[1]}`));
  const rq = {}; for (const r of reqs.slice(1)) rq[`${r[0]}|${r[1]}`] = r;
  let met = 0, total = 0;
  const rows = sched.slice(1).map(r => h("tr", {}, h("td", {class: "nm"}, r[0]), r.slice(1).map((v, i) => {
    const key = `${r[0]}|${days[i]}`, q = rq[key];
    let cls = `cv-${CATS[cat[v]] ? cat[v] : "other"}`;
    if (fx.has(key)) cls += " fx";
    if (q) { total++; const ok = q[2] === "休み" ? !work[v] : v === q[3]; if (ok) met++; cls += ok ? " rq-ok" : " rq-ng"; }
    return h("td", {class: cls + (isWeekend(days[i]) ? " wk" : ""), title: `${r[0]} ${days[i]}${fx.has(key) ? "（固定）" : ""}${q ? "（要望: " + q[2] + (q[3] ? " " + q[3] : "") + "）" : ""}`}, v);
  })));
  const tbl = h("table", {class: "sched"}, h("thead", {}, h("tr", {}, h("th", {}, "氏名"), days.map(d => h("th", {class: isWeekend(d) ? "wk" : ""}, d.slice(5), h("br"), "日月火水木金土"[new Date(d + "T00:00").getDay()])))), h("tbody", {}, rows));
  const legend = h("div", {class: "legend"}, ...Object.entries(CATS).map(([k, v]) => h("span", {class: `cv-${k}`}, v)),
    h("span", {class: "fx"}, "太枠(灰)=固定割当"), h("span", {style: "outline:2px solid var(--ok)"}, "緑枠=要望を満たした"), h("span", {style: "outline:2px solid var(--ng)"}, "赤枠=要望を満たせなかった"));
  return [h("p", {}, total ? `要望 ${total} 件のうち ${met} 件（${Math.round(100 * met / total)}%）を満たしています。` : "要望はありません。"), legend, h("div", {class: "scroll"}, tbl)];
}
const isWeekend = d => [0, 6].includes(new Date(d + "T00:00").getDay());

// ---------------------------------------------------------------- 矛盾する制約
function conflictView(rows, st) {
  const data = rows.slice(1).map(r => ({kind: r[0], id: r[1], group: r[2], title: r[3], layer: r[4], msg: r[5]}));
  const wrap = h("div", {});
  let kind = "", group = "", text = "";
  const draw = () => {
    const shown = data.filter(x => (!kind || x.kind === kind) && (!group || x.group === group) && (!text || (x.id + x.title + x.msg).includes(text)));
    const groups = [...new Set(data.map(x => x.group))];
    wrap.replaceChildren(
      h("p", {}, st.hard_only === "1" ? "オフにできない制約同士（入力データと法令）の矛盾です。入力データを見直してください。"
        : "次の制約は同時には成り立ちません。「外せる」制約はパラメータの調整かオフで緩められます。「前提」はオフにできないので、入力データを見直します。",
        st.minimal === "0" ? " （時間制限で打ち切ったため、最小でない可能性があります）" : ""),
      h("p", {}, ...["", "外せる", "前提"].map(k => h("span", {class: "chip" + (kind === k ? " on" : ""), onclick: () => { kind = k; draw(); }}, k ? `${k} ${data.filter(x => x.kind === k).length}件` : `すべて ${data.length}件`)),
        " ", ...groups.map(g => h("span", {class: "chip" + (group === g ? " on" : ""), onclick: () => { group = group === g ? "" : g; draw(); }}, `${g} ${data.filter(x => x.group === g).length}`)),
        " ", h("input", {type: "text", placeholder: "絞り込み（ID・メッセージ）", value: text, oninput: e => { text = e.target.value; draw2(); }})),
      h("div", {class: "scroll"}, h("table", {class: "conf"}, h("thead", {}, h("tr", {}, ["区分", "ID", "制約クラス", "層", "メッセージ"].map(x => h("th", {}, x)))),
        h("tbody", {}, shown.map(x => h("tr", {}, h("td", {}, x.kind), h("td", {}, h("code", {}, x.id)), h("td", {}, x.title), h("td", {}, x.layer), h("td", {}, x.msg)))))));
  };
  const draw2 = () => { const s = $("input[type=text]", wrap), pos = s.selectionStart; draw(); const n = $("input[type=text]", wrap); n.focus(); n.setSelectionRange(pos, pos); };
  if (!data.length) return h("div", {}, h("p", {}, "矛盾は確定しましたが、原因の制約を時間内に特定できませんでした。"), h("pre", {}, st.message));
  draw(); return wrap;
}

// ---------------------------------------------------------------- 画面の切り替え
function switchTab(t) {
  for (const b of document.querySelectorAll("#tabs button")) b.classList.toggle("on", b.dataset.tab === t);
  for (const s of ["data", "params", "run"]) $(`#tab-${s}`).hidden = s !== t;
  if (t === "run") renderHistory();
}
document.querySelectorAll("#tabs button").forEach(b => b.addEventListener("click", () => switchTab(b.dataset.tab)));
$("#save").addEventListener("click", async () => { try { await saveAll(); toast("保存しました"); } catch (e) { toast("保存できません: " + e.message, 5000); } });
$("#runbtn").addEventListener("click", runEngine);
$("#reset").addEventListener("click", async () => {
  const nm = h("input", {type: "number", id: "rs-n", value: 30, min: 4, style: "width:5em"});
  const ym = h("input", {type: "month", id: "rs-m", value: nextMonth()});
  if (!await ask("見本データに戻しますか？", [h("p", {}, "編集中の入力 CSV は、見本データで置き換わります。"), h("p", {}, "看護師数 ", nm, "（4以上。常勤:パート = 26:4 の比）"), h("p", {}, "対象月 ", ym)], "戻す")) return;
  try { await api("POST", "/api/init", {nurses: +nm.value, month: ym.value}); await loadAll(); toast("見本データで作り直しました"); } catch (e) { toast("作り直せません: " + e.message, 5000); }
});
const nextMonth = () => { const d = new Date(); return `${d.getFullYear() + Math.floor((d.getMonth() + 1) / 12)}-${String((d.getMonth() + 1) % 12 + 1).padStart(2, "0")}`; };
loadAll().then(async () => { const runs = await api("GET", "/api/runs"); if (runs.length) { selected = runs[0].id; } }).catch(e => toast("読み込めません: " + e.message, 8000));
