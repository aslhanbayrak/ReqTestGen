"""Self-contained HTML outputs: the generated test document, the evaluation report and the
cross-validation summary. No external CSS/JS, so the files open on an offline machine."""
from __future__ import annotations

import datetime as _dt
from html import escape

from .doors import Requirement, norm_id
from .generate import GenerationResult

CSS = """
body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#1d2330;font-size:14px;line-height:1.45}
h1{font-size:22px;margin:0 0 4px} h2{font-size:18px;margin:28px 0 8px;border-bottom:2px solid #d8dde6;padding-bottom:4px}
h3{font-size:15px;margin:18px 0 6px} .muted{color:#667085} .small{font-size:12px}
table{border-collapse:collapse;width:100%;margin:6px 0 12px} th,td{border:1px solid #d0d5dd;padding:5px 8px;vertical-align:top;text-align:left}
th{background:#f2f4f7;font-weight:600} td.pre{white-space:pre-wrap;font-family:Consolas,monospace;font-size:12.5px}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0} .card{border:1px solid #d0d5dd;border-radius:6px;padding:8px 14px;min-width:130px}
.card b{display:block;font-size:20px} .req{border:1px solid #d0d5dd;border-radius:6px;padding:10px 14px;margin:14px 0;page-break-inside:avoid}
.reqtext{white-space:pre-wrap;background:#f9fafb;border-left:3px solid #98a2b3;padding:6px 10px;margin:6px 0}
.tag{display:inline-block;border-radius:10px;padding:0 8px;font-size:12px;margin-left:6px;background:#eef2ff;color:#3538cd}
.tag.robustness{background:#fef3f2;color:#b42318} .tag.boundary{background:#fffaeb;color:#b54708}
.warn{background:#fffaeb;border-left:3px solid #f79009;padding:4px 10px;margin:4px 0}
.err{background:#fef3f2;border-left:3px solid #d92d20;padding:4px 10px;margin:4px 0}
.issue{background:#f0f9ff;border-left:3px solid #0086c9;padding:4px 10px;margin:4px 0}
.good{color:#067647;font-weight:600} .mid{color:#b54708;font-weight:600} .bad{color:#b42318;font-weight:600}
a{color:#3538cd;text-decoration:none} @media print{a{color:inherit}}
"""


def _page(title: str, body: str) -> str:
    return (f"<!DOCTYPE html>\n<html lang=\"tr\"><head><meta charset=\"utf-8\"><title>{escape(title)}</title>"
            f"<style>{CSS}</style></head><body>{body}</body></html>\n")


def _anchor(s: str) -> str:
    return "r-" + "".join(ch if ch.isalnum() else "-" for ch in s)


def _pct(v) -> str:
    if v is None:
        return "<span class='muted'>-</span>"
    cls = "good" if v >= 0.75 else "mid" if v >= 0.5 else "bad"
    return f"<span class='{cls}'>{v * 100:.0f}%</span>"


def _cards(items) -> str:
    return "<div class='cards'>" + "".join(f"<div class='card'><b>{v}</b>{escape(k)}</div>" for k, v in items) + "</div>"


# ------------------------------------------------------------ test document
def test_document_html(title: str, requirements: list[Requirement], results: list[GenerationResult],
                       run_info: dict) -> str:
    req_by_id = {norm_id(r.id): r for r in requirements}
    ok = [r for r in results if r.status == "ok"]
    n_cases = sum(len(r.test_cases) for r in ok)
    body = [f"<h1>{escape(title)}</h1>",
            f"<div class='muted'>Otomatik üretilmiş taslak test dokümanı - kullanmadan önce mühendis incelemesi "
            f"zorunludur. {escape(_dt.datetime.now().strftime('%d.%m.%Y %H:%M'))}</div>",
            _cards([("gereksinim", len(results)), ("test case", n_cases),
                    ("uyarılı gereksinim", sum(1 for r in ok if r.warnings)),
                    ("açık konulu gereksinim", sum(1 for r in ok if r.open_issues)),
                    ("hata", len(results) - len(ok))])]
    if run_info:
        body.append("<div class='small muted'>" + " · ".join(f"{escape(k)}: {escape(str(v))}"
                                                            for k, v in run_info.items()) + "</div>")

    body.append("<h2>İzlenebilirlik</h2><table><tr><th>Gereksinim</th><th>Bölüm</th><th>Test case</th>"
                "<th>Uyarı</th><th>Açık konu</th></tr>")
    for res in results:
        req = req_by_id.get(norm_id(res.requirement_id))
        cases = "<br>".join(f"{escape(f'TC{n}')}: {escape(c['title'])}" for n, c in enumerate(res.test_cases, 1)) \
            if res.status == "ok" else "<span class='bad'>üretilemedi</span>"
        body.append(f"<tr><td><a href='#{_anchor(res.requirement_id)}'>{escape(res.requirement_id)}</a></td>"
                    f"<td>{escape(req.section_text if req else '')}</td><td>{cases}</td>"
                    f"<td>{len(res.warnings) or ''}</td><td>{len(res.open_issues) or ''}</td></tr>")
    body.append("</table><h2>Test case'ler</h2>")

    for res in results:
        req = req_by_id.get(norm_id(res.requirement_id))
        body.append(f"<div class='req' id='{_anchor(res.requirement_id)}'><h3>{escape(res.requirement_id)}"
                    f"{(' <span class=muted>- ' + escape(req.section_text) + '</span>') if req and req.section else ''}</h3>")
        if req:
            body.append(f"<div class='reqtext'>{escape(req.text)}</div>")
        if res.status != "ok":
            body.append(f"<div class='err'>Üretilemedi: {escape(res.error)}</div></div>")
            continue
        if res.analysis:
            body.append(f"<div><b>Analiz:</b> {escape(res.analysis)}</div>")
        for w in res.warnings:
            body.append(f"<div class='warn'>{escape(w)}</div>")
        for i in res.open_issues:
            body.append(f"<div class='issue'>Açık konu: {escape(i)}</div>")
        for n, c in enumerate(res.test_cases, 1):
            body.append(f"<h3>TC{n}: {escape(c['title'])}<span class='tag {escape(c['category'])}'>"
                        f"{escape(c['category'])}</span></h3>"
                        "<table><tr><th style='width:110px'>Tür</th><th>Object Text</th><th>Expected Results</th></tr>")
            k = 0
            for kind, label in (("preconditions", "Precondition"), ("steps", "Step")):
                for st in c[kind]:
                    k += 1
                    body.append(f"<tr><td>{label} {k}</td><td class='pre'>{escape(st['object_text'])}</td>"
                                f"<td class='pre'>{escape(st['expected_results'])}</td></tr>")
            body.append("</table>")
        if res.examples or res.docs:
            ex = ", ".join(f"{e['project']}/{e['requirement_id']}" for e in res.examples) or "-"
            body.append(f"<div class='small muted'>Örnek alınan testler: {escape(ex)}"
                        + (f"<br>Kullanılan doküman bölümleri: {escape('; '.join(res.docs))}" if res.docs else "")
                        + "</div>")
        body.append("</div>")
    return _page(title, "\n".join(body))


# ------------------------------------------------------------ evaluation
_METRICS = [
    ("coverage", "Kapsama", "Test üretilen gereksinim oranı"),
    ("signal_recall", "Sinyal recall", "Gerçek testlerdeki sinyallerin üretilen testlerde geçme oranı"),
    ("signal_precision", "Sinyal precision", "Üretilen sinyallerin gerçek testlerde de geçme oranı"),
    ("check_recall", "Kontrol recall", "Gerçek 'sinyal = beklenen değer' kontrollerinin bulunma oranı"),
    ("stimulus_recall", "Uyarım recall", "Gerçek 'sinyal = verilen değer' girdilerinin bulunma oranı"),
    ("term_recall", "Terim recall", "Gerçek testlerdeki anahtar adların (SMFD-3, LSK1, \"HDG REF\" ...) üretilende geçme oranı"),
    ("term_precision", "Terim precision", "Üretilen anahtar adların gerçek testlerde de geçme oranı"),
    ("case_match_recall", "Test case eşleşme", "Benzerliği eşiğin üstünde bir karşılığı olan gerçek test case oranı"),
    ("judge_recall", "Hakem recall", "Hakem modele göre karşılanan gerçek test case oranı (kısmen = yarım)"),
]


def evaluation_html(title: str, ev: dict, results: list[GenerationResult], reference: dict) -> str:
    s = ev["summary"]
    res_by_id = {norm_id(r.requirement_id): r for r in results}
    ref_by_id = {norm_id(k): v for k, v in reference.items()}
    body = [f"<h1>{escape(title)}</h1>",
            _cards([("değerlendirilen gereksinim", s["evaluated_requirements"]),
                    ("gerçek test case", s["reference_test_cases"]),
                    ("üretilen test case", s["generated_test_cases"])]),
            "<h2>Özet</h2><table><tr><th>Ölçüt</th><th>Değer</th><th>Anlamı</th></tr>"]
    for key, label, desc in _METRICS:
        if s.get(key) is not None or key != "judge_recall":
            body.append(f"<tr><td>{label}</td><td>{_pct(s.get(key))}</td><td class='small'>{escape(desc)}</td></tr>")
    body.append("</table>")
    body.append(f"<div class='small muted'>Benzerlik: {escape(s['similarity'])}, eşik {s['match_threshold']}."
                + (f" Hakemin geçersiz bulduğu üretilmiş test case: {s['judge_invalid_cases']}." if s.get('judge_invalid_cases') is not None else "")
                + "</div>")

    body.append("<h2>Gereksinim bazında</h2><table><tr><th>Gereksinim</th><th>Gerçek / üretilen</th>"
                "<th>Sinyal R</th><th>Sinyal P</th><th>Kontrol R</th><th>Uyarım R</th><th>Terim R</th><th>Terim P</th>"
                "<th>Eşleşme</th><th>Hakem</th><th>Eksik sinyal / terim</th></tr>")
    for r in ev["requirements"]:
        missing = (r.get("missing_signals") or [])[:8] + (r.get("missing_terms") or [])[:8]
        body.append(f"<tr><td><a href='#{_anchor(r['requirement_id'])}'>{escape(r['requirement_id'])}</a></td>"
                    f"<td>{r['ref_cases']} / {r['gen_cases']}</td><td>{_pct(r['signal_recall'])}</td>"
                    f"<td>{_pct(r['signal_precision'])}</td><td>{_pct(r['check_recall'])}</td>"
                    f"<td>{_pct(r['stimulus_recall'])}</td><td>{_pct(r.get('term_recall'))}</td>"
                    f"<td>{_pct(r.get('term_precision'))}</td><td>{_pct(r['case_match_recall'])}</td>"
                    f"<td>{_pct(r['judge_recall'])}</td><td class='small'>{escape(', '.join(missing))}</td></tr>")
    body.append("</table><h2>Yan yana karşılaştırma</h2>")

    for r in ev["requirements"]:
        rid = norm_id(r["requirement_id"])
        body.append(f"<div class='req' id='{_anchor(r['requirement_id'])}'><h3>{escape(r['requirement_id'])}</h3>")
        j = r.get("judge") or {}
        if j.get("overall"):
            body.append(f"<div class='issue'>Hakem: {escape(str(j['overall']))}</div>")
        verdicts = {x.get("index"): x for x in j.get("reference", []) if isinstance(x, dict)}
        body.append("<table><tr><th style='width:50%'>Gerçek testler</th><th>Üretilen testler</th></tr><tr><td>")
        for n, c in enumerate(ref_by_id.get(rid, []), 1):
            v = verdicts.get(n, {})
            sim = r["best_similarities"][n - 1] if n - 1 < len(r["best_similarities"]) else None
            body.append(f"<b>[{n}] {escape(c.title)}</b>"
                        + (f" <span class='tag'>{escape(str(v.get('verdict')))}</span>" if v.get("verdict") else "")
                        + (f" <span class='small muted'>benzerlik {sim}</span>" if sim is not None else "")
                        + (f"<div class='small muted'>{escape(str(v.get('reason')))}</div>" if v.get("reason") else "")
                        + "<div class='pre small' style='white-space:pre-wrap'>"
                        + escape("\n".join(f"{s.object_text}\n  => {s.expected_results}" for s in c.steps)) + "</div><br>")
        body.append("</td><td>")
        res = res_by_id.get(rid)
        for n, c in enumerate(res.test_cases if res else [], 1):
            steps = c["preconditions"] + c["steps"]
            body.append(f"<b>[{n}] {escape(c['title'])}</b><span class='tag {escape(c['category'])}'>{escape(c['category'])}</span>"
                        "<div class='pre small' style='white-space:pre-wrap'>"
                        + escape("\n".join(f"{s['object_text']}\n  => {s['expected_results']}" for s in steps)) + "</div><br>")
        body.append("</td></tr></table></div>")
    return _page(title, "\n".join(body))


def crossval_html(title: str, rows: list[dict]) -> str:
    keys = [k for k, _, _ in _METRICS]
    body = [f"<h1>{escape(title)}</h1>",
            "<div class='muted'>Her proje, örnek olarak yalnızca diğer projelerin testleri kullanılarak üretildi ve "
            "kendi gerçek testleriyle karşılaştırıldı.</div>",
            "<table><tr><th>Proje</th><th>Gereksinim</th><th>Gerçek / üretilen TC</th>"
            + "".join(f"<th>{label}</th>" for _, label, _ in _METRICS) + "<th>Rapor</th></tr>"]
    for r in rows:
        s = r["summary"]
        body.append(f"<tr><td>{escape(r['project'])}</td><td>{s['evaluated_requirements']}</td>"
                    f"<td>{s['reference_test_cases']} / {s['generated_test_cases']}</td>"
                    + "".join(f"<td>{_pct(s.get(k))}</td>" for k in keys)
                    + f"<td><a href='{escape(r['report'])}'>aç</a></td></tr>")
    body.append("</table><h3>Ölçütler</h3><ul>" + "".join(f"<li><b>{label}</b>: {escape(desc)}</li>"
                                                         for _, label, desc in _METRICS) + "</ul>")
    return _page(title, "\n".join(body))
