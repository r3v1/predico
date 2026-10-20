/* Informe ejecutivo · Predico 50Hertz. Todo se pinta desde window.REPORT_DATA (generado por Python). */
(function () {
  'use strict';
  var D = window.REPORT_DATA, GEO = window.REPORT_GEO;
  var Q = new URLSearchParams(location.search);
  var REDUCED = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  var NOANIM = Q.has('noanim') || REDUCED;
  if (Q.has('noanim')) document.documentElement.classList.add('noanim');
  var ANIM = !NOANIM;
  var DAY = 86400000;
  var MONTHS = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];
  var MON3 = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
  var Z = D.zone, P = D.plan, QL = D.quality, PR = D.production || {}, CK = D.clock, VOL = D.volumes || {};

  /* ---------- utilidades ---------- */
  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function num(x, d) {
    if (x == null || isNaN(x)) return '—';
    d = d || 0;
    return Number(x).toLocaleString('de-DE', { minimumFractionDigits: d, maximumFractionDigits: d });
  }
  function bytes(n) {
    if (n == null) return null;
    var u = [['TB', 1e12], ['GB', 1e9], ['MB', 1e6], ['kB', 1e3]];
    for (var i = 0; i < u.length; i++) if (n >= u[i][1]) { var v = n / u[i][1]; return { v: num(v, v >= 100 ? 0 : 1), u: u[i][0] }; }
    return { v: num(n), u: 'B' };
  }
  function bytesTxt(n) { var b = bytes(n); return b ? b.v + ' ' + b.u : 'no disponible'; }
  function pd(iso) { var p = String(iso).slice(0, 10).split('-'); return Date.UTC(+p[0], +p[1] - 1, +p[2]); }
  function dateLong(iso) { var d = new Date(pd(iso)); return d.getUTCDate() + ' de ' + MONTHS[d.getUTCMonth()] + ' de ' + d.getUTCFullYear(); }
  function dateShort(iso) { var d = new Date(pd(iso)); return d.getUTCDate() + ' ' + MON3[d.getUTCMonth()]; }
  function dmy(ms) { var d = new Date(ms); return ('0' + d.getUTCDate()).slice(-2) + '/' + ('0' + (d.getUTCMonth() + 1)).slice(-2) + '/' + d.getUTCFullYear(); }
  function hm(ms) { var d = new Date(ms); return ('0' + d.getUTCHours()).slice(-2) + ':' + ('0' + d.getUTCMinutes()).slice(-2); }
  function monthYear(ms) { var d = new Date(ms); return MONTHS[d.getUTCMonth()] + ' de ' + d.getUTCFullYear(); }
  function alpha(hex, a) {
    var h = hex.replace('#', '');
    if (h.length === 3) h = h.replace(/(.)/g, '$1$1');
    return 'rgba(' + parseInt(h.slice(0, 2), 16) + ',' + parseInt(h.slice(2, 4), 16) + ',' + parseInt(h.slice(4, 6), 16) + ',' + a + ')';
  }
  function mix(rgb, to, t) { return 'rgb(' + rgb.map(function (v, i) { return Math.round(v + (to[i] - v) * t); }).join(',') + ')'; }
  function tfmt(v) { var d = new Date(v); return d.getUTCMonth() === 0 && d.getUTCDate() === 1 ? String(d.getUTCFullYear()) : (d.getUTCDate() > 1 ? d.getUTCDate() + ' ' : '') + MON3[d.getUTCMonth()]; }
  function empty(el, msg) { el.innerHTML = '<div class="empty">' + esc(msg) + '</div>'; }
  function isDark() { return document.documentElement.getAttribute('data-theme') === 'dark'; }
  function isMobile() { return window.innerWidth < 860; }
  function cv(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
  function C() {
    var o = {};
    ['paper', 'paper-2', 'card', 'ink', 'ink-2', 'muted', 'line', 'line-2', 'wind', 'wind-soft', 'sea', 'amber', 'coral', 'ok', 'pending'].forEach(function (k) { o[k.replace('-', '')] = cv('--' + k); });
    return o;
  }
  function path(obj, p) { return p.split('.').reduce(function (a, k) { return a == null ? null : a[k]; }, obj); }
  function fmtv(v, f) {
    if (v == null) return '—';
    if (f === 'dec1') return num(v, 1);
    if (f === 'date') return dateLong(v);
    if (f === 'bytes') return bytesTxt(v);
    return num(v, 0);
  }
  function gl(id, text) { return '<span class="gl" tabindex="0" data-gl="' + id + '">' + text + '</span>'; }

  /* ---------- derivados ---------- */
  D.derived = {
    n_land: Z ? Z.groups.filter(function (g) { return g.kind === 'land'; }).length : null,
    n_sea: Z ? Z.groups.filter(function (g) { return g.kind === 'sea'; }).length : null,
    n_cells: Z ? Z.domain.n_lat * Z.domain.n_lon : null,
    lead_first: D.forecast ? D.forecast.leads[0] : null,
    lead_last: D.forecast ? D.forecast.leads[D.forecast.leads.length - 1] : null,
    lead_step: D.forecast && D.forecast.leads.length > 1 ? D.forecast.leads[1] - D.forecast.leads[0] : null
  };
  var nwpRow = QL && QL.rows.filter(function (r) { return r.key === 'nwp'; })[0];

  /* ---------- proyección Mercator (conserva las proporciones) ---------- */
  var MERC = {
    project: function (p) { return [p[0] * Math.PI / 180, -Math.log(Math.tan(Math.PI / 4 + p[1] * Math.PI / 360))]; },
    unproject: function (p) { return [p[0] * 180 / Math.PI, (2 * Math.atan(Math.exp(-p[1])) - Math.PI / 2) * 180 / Math.PI]; }
  };
  function aspect(v) { var a = MERC.project([v[0], v[3]]), b = MERC.project([v[2], v[1]]); return (b[0] - a[0]) / (b[1] - a[1]); }
  function fitHeight(id, v) { var el = document.getElementById(id); if (el) el.style.height = Math.round(el.clientWidth / aspect(v)) + 'px'; }
  var MAPS = {};
  /* Recorta un GeoJSON a la vista (Sutherland-Hodgman) y añade un marco invisible para que el mapa ocupe exactamente la vista. */
  function clipRing(ring, b) {
    var edges = [
      [function (p) { return p[0] >= b[0]; }, function (a, c) { return [b[0], a[1] + (c[1] - a[1]) * (b[0] - a[0]) / (c[0] - a[0])]; }],
      [function (p) { return p[0] <= b[2]; }, function (a, c) { return [b[2], a[1] + (c[1] - a[1]) * (b[2] - a[0]) / (c[0] - a[0])]; }],
      [function (p) { return p[1] >= b[1]; }, function (a, c) { return [a[0] + (c[0] - a[0]) * (b[1] - a[1]) / (c[1] - a[1]), b[1]]; }],
      [function (p) { return p[1] <= b[3]; }, function (a, c) { return [a[0] + (c[0] - a[0]) * (b[3] - a[1]) / (c[1] - a[1]), b[3]]; }]
    ];
    var out = ring.slice(0, -1);
    for (var e = 0; e < 4; e++) {
      var src = out; out = [];
      for (var i = 0; i < src.length; i++) {
        var cur = src[i], prev = src[(i + src.length - 1) % src.length];
        if (edges[e][0](cur)) { if (!edges[e][0](prev)) out.push(edges[e][1](prev, cur)); out.push(cur); }
        else if (edges[e][0](prev)) out.push(edges[e][1](prev, cur));
      }
      if (!out.length) return null;
    }
    out.push(out[0]);
    return out;
  }
  function regMap(src, v) {
    var name = src + ':' + v.join(',');
    if (MAPS[name]) return name;
    var keep = [];
    var feats = [];
    GEO[src].features.forEach(function (f) {
      var polys = [];
      f.geometry.coordinates.forEach(function (poly) {
        var rings = poly.map(function (r) { return clipRing(r, v); }).filter(function (r) { return r && r.length >= 4; });
        if (rings.length) polys.push(rings);
      });
      if (polys.length) feats.push({ type: 'Feature', properties: f.properties, geometry: { type: 'MultiPolygon', coordinates: polys } });
    });
    feats.push({ type: 'Feature', properties: { name: '__marco' }, geometry: { type: 'Polygon', coordinates: [[[v[0], v[1]], [v[2], v[1]], [v[2], v[3]], [v[0], v[3]], [v[0], v[1]]]] } });
    echarts.registerMap(name, { type: 'FeatureCollection', features: feats });
    MAPS[name] = feats;
    return name;
  }
  /* Costa y fronteras como líneas finas, para dibujarlas por encima de un campo de color. */
  function borderLines(src, v, c) {
    var feats = MAPS[regMap(src, v)], thin = [], zone = [];
    feats.forEach(function (f) {
      if (f.properties.name === '__marco') return;
      f.geometry.coordinates.forEach(function (poly) { poly.forEach(function (r) { (f.properties.zone ? zone : thin).push({ coords: r }); }); });
    });
    var dk = isDark();
    return [
      { type: 'lines', coordinateSystem: 'geo', silent: true, z: 4, polyline: true, animation: false, data: thin, lineStyle: { color: dk ? 'rgba(236,240,246,.62)' : 'rgba(16,35,59,.55)', width: 0.8 } },
      { type: 'lines', coordinateSystem: 'geo', silent: true, z: 4, polyline: true, animation: false, data: zone, lineStyle: { color: dk ? '#ffffff' : c.ink, width: 1.7, opacity: dk ? 0.9 : 0.85 } }
    ];
  }
  var FRAME = { name: '__marco', silent: true, itemStyle: { areaColor: 'rgba(0,0,0,0)', borderWidth: 0, borderColor: 'rgba(0,0,0,0)' }, label: { show: false }, emphasis: { disabled: true } };
  function geoBox(src, v, extra) {
    var regs = ((extra && extra.regions) || []).concat([FRAME]);
    return Object.assign({ roam: false, silent: true }, extra || {}, { map: regMap(src, v), regions: regs, projection: MERC, left: 0, right: 0, top: 0, bottom: 0 });
  }
  function inRing(x, y, r) { var c = false, i, j; for (i = 0, j = r.length - 1; i < r.length; j = i++) { if ((r[i][1] > y) !== (r[j][1] > y) && x < (r[j][0] - r[i][0]) * (y - r[i][1]) / (r[j][1] - r[i][1]) + r[i][0]) c = !c; } return c; }
  function inFeature(x, y, f) { return f.geometry.coordinates.some(function (poly) { return inRing(x, y, poly[0]) && !poly.slice(1).some(function (h) { return inRing(x, y, h); }); }); }

  /* ---------- tema y gráficos ---------- */
  var reg = {};
  var FONT = "Inter, system-ui, sans-serif";
  function base(c) {
    return {
      useUTC: true, animation: ANIM, animationDuration: 700, animationEasing: 'cubicOut',
      textStyle: { fontFamily: FONT, color: c.ink2 },
      tooltip: {
        confine: true, backgroundColor: c.card, borderColor: c.line2, borderWidth: 1, padding: [10, 12],
        textStyle: { color: c.ink, fontFamily: FONT, fontSize: 13, lineHeight: 19 },
        extraCssText: 'border-radius:10px;box-shadow:0 8px 28px rgba(8,18,32,.16);'
      }
    };
  }
  function axisBase(c) {
    return {
      axisLine: { lineStyle: { color: c.line2 } }, axisTick: { lineStyle: { color: c.line2 } },
      axisLabel: { color: c.muted, fontSize: 11.5, fontFamily: FONT }, splitLine: { lineStyle: { color: c.line } }
    };
  }
  function mount(id, build, opts) {
    var el = document.getElementById(id);
    if (!el || typeof echarts === 'undefined') return;
    if (reg[id] && reg[id].inst) reg[id].inst.dispose();
    reg[id] = { el: el, build: build, opts: opts || {}, inst: null };
    draw(id, true);
  }
  function draw(id, anim) {
    var r = reg[id];
    if (!r || !document.body.contains(r.el)) { delete reg[id]; return; }
    ANIM = anim && !NOANIM;
    if (r.inst) r.inst.dispose();
    r.inst = echarts.init(r.el, null, { renderer: 'canvas' });
    var opt = r.build(C());
    opt.animation = ANIM;
    r.inst.setOption(opt);
    if (r.opts.after) r.opts.after(r.inst);
  }
  function redrawAll(anim) { Object.keys(reg).forEach(function (id) { draw(id, anim); }); }

  /* ---------- tema ---------- */
  function setTheme(t, persist) {
    document.documentElement.setAttribute('data-theme', t);
    if (persist) { try { localStorage.setItem('predico-theme', t); } catch (e) { /* sin almacenamiento */ } }
    redrawAll(false);
  }
  $('#themeBtn').addEventListener('click', function () { setTheme(isDark() ? 'light' : 'dark', true); });

  /* ---------- enlaces de datos ---------- */
  $$('[data-v]').forEach(function (e) { e.textContent = fmtv(path(D, e.getAttribute('data-v')), e.getAttribute('data-f')); });

  /* ---------- navegación ---------- */
  (function () {
    var nav = $('#navLinks');
    $$('section[data-nav]').forEach(function (s, i) {
      var a = document.createElement('a');
      a.href = '#' + s.id; a.setAttribute('data-for', s.id);
      a.innerHTML = '<i>' + (i + 1) + '</i>' + esc(s.getAttribute('data-nav'));
      nav.appendChild(a);
    });
    if (!('IntersectionObserver' in window)) return;
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        if (e.isIntersecting) {
          $$('a', nav).forEach(function (a) {
            var on = a.getAttribute('data-for') === e.target.id;
            a.classList.toggle('on', on);
            if (on && nav.scrollWidth > nav.clientWidth) nav.scrollTo({ left: a.offsetLeft - 60, behavior: 'smooth' });
          });
        }
      });
    }, { rootMargin: '-45% 0px -50% 0px' });
    $$('section[data-nav]').forEach(function (s) { io.observe(s); });
  })();

  $$('section[data-nav] .ch-num').forEach(function (e, i) { e.textContent = ('0' + (i + 1)).slice(-2); });

  /* ---------- aparición suave ---------- */
  (function () {
    var els = $$('.reveal');
    if (NOANIM || !('IntersectionObserver' in window)) { els.forEach(function (e) { e.classList.add('in'); }); return; }
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.04 });
    els.forEach(function (e) { io.observe(e); });
  })();

  /* ---------- glosario ---------- */
  (function () {
    var pop = $('#gl-pop'), terms = {};
    (D.glossary || []).forEach(function (t) { terms[t.id] = t; });
    function show(el) {
      var t = terms[el.getAttribute('data-gl')];
      if (!t) return;
      pop.innerHTML = '<b>' + esc(t.term) + '</b>' + esc(t.text);
      var r = el.getBoundingClientRect();
      var w = Math.min(300, window.innerWidth - 24);
      pop.style.maxWidth = w + 'px';
      var left = Math.max(12, Math.min(window.innerWidth - w - 12, r.left + r.width / 2 - w / 2)) + window.scrollX;
      pop.style.left = left + 'px';
      pop.style.top = '0px';
      pop.classList.add('show');
      var h = pop.offsetHeight;
      var top = r.top - h - 10;
      if (top < 64) top = r.bottom + 10;
      pop.style.top = (top + window.scrollY) + 'px';
    }
    function hide() { pop.classList.remove('show'); }
    document.addEventListener('mouseover', function (e) { var g = e.target.closest && e.target.closest('.gl'); if (g) show(g); });
    document.addEventListener('mouseout', function (e) { if (e.target.closest && e.target.closest('.gl')) hide(); });
    document.addEventListener('focusin', function (e) { var g = e.target.closest && e.target.closest('.gl'); if (g) show(g); });
    document.addEventListener('focusout', hide);
    document.addEventListener('click', function (e) { var g = e.target.closest && e.target.closest('.gl'); if (g) show(g); else hide(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') hide(); });
    window.addEventListener('scroll', hide, { passive: true });
  })();

  /* ---------- portada ---------- */
  (function () {
    var gen = new Date(D.meta.generated_at);
    var madrid = new Intl.DateTimeFormat('es-ES', { dateStyle: 'long', timeStyle: 'short', timeZone: 'Europe/Madrid' }).format(gen);
    $('#stamp').textContent = 'Generado el ' + madrid.replace(',', ' a las') + ' (hora de Madrid)' + (P ? ' · Plan: ' + planSrc() : '');
    $('#kpis').innerHTML = D.kpis.map(function (k) {
      var v = k.value == null ? '—' : num(k.value, k.fmt === 'dec1' ? 1 : 0);
      return '<div class="kpi"><div class="v tn">' + v + (k.unit && k.value != null ? '<small>' + esc(k.unit) + '</small>' : '') + '</div><div class="l">' + esc(k.label) + '</div></div>';
    }).join('');
  })();

  /* ---------- ideas clave ---------- */
  (function () {
    var ideas = {};
    ideas.i1 = P ? (function () {
      // Lo cumplido (hitos y etapas de datos) y lo que queda (tareas de GitHub), por separado:
      // lo hecho antes de pasar a GitHub no tiene tareas cerradas y no debe contar como cero.
      var ms = P.milestones, st = P.stages || [];
      var hit = ms.filter(function (m) { return m.status === 'done'; }).length;
      var stOk = st.filter(function (x) { return x.status === 'done'; }).length;
      var closed = 0, total = 0;
      ms.forEach(function (m) { if (m.total) { closed += m.closed; total += m.total; } });
      return 'Faltan <b>' + num(P.days_to_league) + '</b> días para la liga. Ya hay <b>' + hit + '</b> ' + (hit === 1 ? 'hito cumplido' : 'hitos cumplidos') +
        (st.length ? ' y <b>' + stOk + ' de ' + st.length + '</b> etapas de datos descargadas' : '') +
        '; del trabajo que queda, <b>' + closed + ' de ' + total + '</b> tareas cerradas.';
    })() : 'No se ha podido leer el plan del proyecto.';
    ideas.i2 = Z ? 'La meteorología se calcula en <b>' + num(Z.n_groups) + '</b> puntos que representan a <b>' + num(Z.n_turbines) + '</b> aerogeneradores y <b>' + num(Z.mw_total / 1000, 1) + ' GW</b> de potencia.' : 'Sin datos del registro de aerogeneradores.';
    ideas.i3 = 'Tres fuentes abiertas y una copia propia de todo: si una cambia, podemos reconstruir lo que hicimos.';
    var v = VOL.nwp || {};
    ideas.i4 = v.download && v.silver ? 'De <b>' + bytesTxt(v.download) + '</b> leídos de la previsión meteorológica, lo útil para el modelo ocupa <b>' + bytesTxt(v.silver) + '</b>: cada paso descarta lo que no hace falta.' : 'Cada paso descarta lo que el modelo no necesita y guarda lo que sí.';
    ideas.i5 = QL ? '<b>' + QL.checks_ok + ' de ' + QL.checks_total + '</b> controles superados' + (QL.coverage != null ? ' y la previsión guardada cubre el <b>' + num(QL.coverage * 100) + ' %</b> de los días de su periodo.' : '.') : 'Sin informes de validación.';
    var d = PR.daily;
    ideas.i6 = d && d.cf_onshore != null ? 'En el último año los parques en tierra produjeron de media el <b>' + num(d.cf_onshore * 100) + ' %</b> de su potencia y los del mar el <b>' + num(d.cf_offshore * 100) + ' %</b>.' : 'La producción real y la potencia instalada, lado a lado.';
    var margin = CK ? (clockInfo(CK.summer_now ? 'summer' : 'winter').marginMin) : null;
    ideas.i7 = CK ? 'La previsión llega con unas <b>' + num(margin / 60, 1) + ' horas</b> de margen antes del cierre, más de <b>' + Math.floor(margin / (CK.margin_required_min || 60)) + ' veces</b> lo que exigimos.' : 'Cuándo llega la previsión y cuánto margen deja antes del cierre.';
    var ins = D.insights || [];
    var lv = function (l) { return ins.filter(function (i) { return i.level === l; }).length; };
    var cnt = function (n, one, many) { return '<b>' + n + '</b> ' + (n === 1 ? one : many); };
    var parts = [cnt(lv('risk'), 'riesgo abierto', 'riesgos abiertos'), cnt(lv('watch'), 'por vigilar', 'por vigilar'),
      cnt(lv('ok'), 'resuelto', 'resueltos'), cnt(lv('info'), 'de contexto', 'de contexto')]
      .filter(function (t) { return t.indexOf('<b>0</b>') !== 0; });
    var last = parts.pop();
    ideas.i8 = ins.length ? '<b>' + ins.length + '</b> hallazgos: ' + (parts.length ? parts.join(', ') + ' y ' + last : last) + '.' : 'Sin hallazgos registrados.';
    var M = D.model || {};
    var dd = M.version_date ? Math.round((pd(M.version_date) - pd(D.meta.today)) / DAY) : null;
    ideas.i9 = dd != null && dd >= 0 ? 'La primera versión del modelo llega en <b>' + dd + '</b> días; ya sabemos con qué vara se va a medir.' : 'Qué se medirá cuando exista el modelo.';
    var BT = D.backtest;
    ideas.i10 = BT && BT.headline && BT.headline.text ? BT.headline.text : 'Aquí se verá, con cifras, si nuestro pronóstico supera al del operador. Todavía no se ha ejecutado ninguna prueba.';
    Object.keys(ideas).forEach(function (k) { var e = document.getElementById('idea' + k.slice(1)); if (e) e.innerHTML = ideas[k]; });
  })();

  /* ============ Capítulo 1: dónde estamos ============ */
  function planSrc() {
    var s = P.source || {}, d = s.fetched ? dateShort(String(s.fetched).slice(0, 10)) : '';
    return s.kind === 'cache' ? 'GitHub (copia del ' + d + ')' : 'GitHub, ' + d;
  }
  function renderTimeline() {
    var el = $('#timeline');
    if (!P || !P.milestones.length) { empty(el, 'No se ha podido leer el calendario (ni de GitHub ni de la caché).'); return; }
    var ms = P.milestones, today = pd(D.meta.today);
    var t0 = Math.min(today, pd(ms[0].date)) - DAY, t1 = pd(P.league_start) + 2 * DAY;
    var W = el.clientWidth, X = function (t) { return (t - t0) / (t1 - t0) * W; };
    var html = '<div class="tl-axis"></div><div class="tl-done" style="width:' + Math.max(0, X(today)) + 'px"></div>';
    var nowDone = false, nowHtml = '<div class="tl-now">Hoy · ' + dateShort(D.meta.today) + '</div>';
    ms.forEach(function (m, i) {
      var a = pd(m.date), b = pd(m.end), mid = (a + b) / 2, x = X(mid), up = i % 2 === 0;
      var lab = Math.max(0, Math.min(W - 170, x - 85));
      if (b > a) html += '<div class="tl-range" style="left:' + X(a) + 'px;width:' + (X(b) - X(a)) + 'px"></div>';
      html += '<div class="tl-stem" style="left:' + x + 'px;' + (up ? 'top:calc(50% - 26px);height:26px' : 'top:50%;height:26px') + '"></div>';
      html += '<div class="tl-dot ' + m.status + (i === ms.length - 1 ? ' final' : '') + '" style="left:' + x + 'px"></div>';
      if (!nowDone && a >= today) { nowDone = true; html += nowHtml; }
      html += '<div class="tl-item ' + (up ? 'up' : 'dn') + '" style="left:' + lab + 'px"><i class="tl-mdot ' + m.status + (i === ms.length - 1 ? ' final' : '') + '"></i>' +
        '<div class="d">' + esc(m.key) + (m.gate ? ' <span class="pill-g">' + esc(m.gate) + '</span>' : '') + '</div>' +
        '<div class="t">' + esc(m.label) + '</div>' + (m.gate_text ? '<div class="g">' + esc(m.gate_text) + '</div>' : '') + '</div>';
    });
    // La etiqueta «Hoy» va en el hueco entre el eje y los textos, del lado con más sitio hasta el hito vecino.
    var tx = X(today), xs = ms.map(function (m) { return X((pd(m.date) + pd(m.end)) / 2); });
    var dl = tx - Math.max.apply(null, [-Infinity].concat(xs.filter(function (x) { return x <= tx; })));
    var dr = Math.min.apply(null, [Infinity].concat(xs.filter(function (x) { return x > tx; }))) - tx;
    html += '<div class="tl-today ' + (dr >= dl ? 'lab-r' : 'lab-l') + '" style="left:' + tx + 'px"><span>Hoy</span></div>';
    el.innerHTML = html;
    $('#rules').innerHTML = P.rules && P.rules.length ? '<div class="rules"><b>Reglas de corte</b>' + P.rules.map(function (r) { return '<span>' + esc(r) + '</span>'; }).join('') + '</div>' : '';
    if (P.source && P.source.kind === 'cache') $('#rules').innerHTML += '<p class="muted">Sin conexión con GitHub: estado de la copia guardada el ' + esc(dateLong(String(P.source.fetched).slice(0, 10))) + '.</p>';
  }
  function renderBlocks() {
    var el = $('#blocks');
    if (!P || !P.blocks.length) { empty(el, 'Sin bloques en el plan.'); return; }
    var lbl = { done: 'Terminado', progress: 'En marcha', pending: 'Pendiente' };
    el.innerHTML = P.blocks.map(function (b) {
      var t = b.total || 0, dn = t ? b.done / t * 100 : 0, pa = t ? (b.partial + b.progress) / t * 100 : 0;
      var cap = t ? b.done + ' de ' + t + ' tareas hechas' + (b.partial + b.progress ? ' · ' + (b.partial + b.progress) + ' a medias' : '') : lbl[b.status];
      return '<div class="block"><div class="top"><span class="light ' + b.status + '" title="' + lbl[b.status] + '"></span><div>' + (b.id ? '<div class="id">' + esc(b.id) + '</div>' : '') + '<div class="nm">' + esc(b.title) + '</div></div></div>' +
        '<div class="bar"><i class="d" style="width:' + dn + '%"></i><i class="p" style="width:' + pa + '%"></i></div><div class="cap">' + esc(cap) + '</div></div>';
    }).join('');
    $('#stages').innerHTML = (P.stages || []).map(function (s) {
      return '<div class="stage ' + s.status + '"><b>Etapa ' + esc(s.id) + '</b>' + esc(s.what) + '</div>';
    }).join('');
  }
  function renderChangelog() {
    var el = $('#changelog'), L = D.changelog || [];
    if (!L.length) { empty(el, 'Sin novedades registradas.'); return; }
    var e = L[0];
    var items = function (x) { return '<ul>' + (x.items || []).map(function (i) { return '<li>' + esc(i) + '</li>'; }).join('') + '</ul>'; };
    el.innerHTML = '<div class="card-title">Novedades de esta versión</div><div class="cl-head"><span class="v">v' + esc(e.version) + '</span><span class="dt">' + esc(dateLong(e.date)) + '</span></div><h3>' + esc(e.title) + '</h3>' + items(e) +
      (L.length > 1 ? '<details><summary>Versiones anteriores (' + (L.length - 1) + ')</summary>' + L.slice(1).map(function (o) {
        return '<div class="old"><div class="cl-head"><span class="v">v' + esc(o.version) + '</span><span class="dt">' + esc(dateLong(o.date)) + '</span></div><h4>' + esc(o.title) + '</h4>' + items(o) + '</div>';
      }).join('') + '</details>' : '');
  }
  renderTimeline(); renderBlocks(); renderChangelog();

  /* ============ Capítulo 2: el mapa ============ */
  var NAME_ES = {
    Berlin: 'Berlín', Brandenburg: 'Brandeburgo', 'Mecklenburg-Vorpommern': 'Mecklemburgo-Pomerania Occ.', Sachsen: 'Sajonia', 'Sachsen-Anhalt': 'Sajonia-Anhalt', 'Thüringen': 'Turingia',
    Niedersachsen: 'Baja Sajonia', Bayern: 'Baviera', Hessen: 'Hesse', Hamburg: 'Hamburgo', 'Nordrhein-Westfalen': 'Renania del N.-Westfalia', 'Rheinland-Pfalz': 'Renania-Palatinado',
    'Baden-Württemberg': 'Baden-Wurtemberg', Saarland: 'Sarre', Poland: 'Polonia', Czechia: 'Chequia', Denmark: 'Dinamarca', Netherlands: 'Países Bajos', Sweden: 'Suecia', Belgium: 'Bélgica',
    Luxembourg: 'Luxemburgo', Switzerland: 'Suiza', Slovakia: 'Eslovaquia', Lithuania: 'Lituania', Russia: 'Rusia', France: 'Francia', Hungary: 'Hungría', Slovenia: 'Eslovenia', Italy: 'Italia',
    Latvia: 'Letonia', Norway: 'Noruega', Belarus: 'Bielorrusia', Ukraine: 'Ucrania', Croatia: 'Croacia', Liechtenstein: 'Liechtenstein', Austria: 'Austria'
  };
  var STATE_ES = { Brandenburg: 'Brandeburgo', 'Mecklenburg-Vorpommern': 'Mecklemburgo-Pomerania Occ.', Sachsen: 'Sajonia', 'Sachsen-Anhalt': 'Sajonia-Anhalt', 'Thüringen': 'Turingia', Ostsee: 'Mar Báltico', Berlin: 'Berlín' };
  var sel = Z && Z.groups.length ? Z.groups[0].id : null;
  var mapState = { mode: 'points', grid: true };
  function groupById(id) { return Z.groups.filter(function (g) { return g.id === id; })[0]; }
  function mapPalette() {
    return isDark()
      ? { sea: '#0a1a2c', land: '#172b43', country: '#13243a', zone: '#15454e', border: '#2c4562', zoneBorder: '#4cc4cc', label: '#b9c6d6', cl: '#6f829a' }
      : { sea: '#e7eef3', land: '#f5f1e8', country: '#ece7dc', zone: '#d4eaeb', border: '#c6bfae', zoneBorder: '#2d8f98', label: '#3d4b5e', cl: '#8b8f96' };
  }
  var ZONE_VIEW = [8.0, 49.1, 16.9, 56.4];
  var LAND_LABEL = { 'Mecklenburg-Vorpommern': 'Mecklemburgo-\nPomerania Occ.', 'Sachsen-Anhalt': 'Sajonia-\nAnhalt' };
  /* Nombres de los estados en huecos libres: el punto del polígono más cercano a su centro que no toque ninguna burbuja ni otro nombre. */
  function placeStateLabels(W, kscale) {
    var p0 = MERC.project([ZONE_VIEW[0], ZONE_VIEW[3]]), p1 = MERC.project([ZONE_VIEW[2], ZONE_VIEW[1]]);
    var sc = W / (p1[0] - p0[0]);
    var px = function (lon, lat) { var q = MERC.project([lon, lat]); return [(q[0] - p0[0]) * sc, (q[1] - p0[1]) * sc]; };
    var circles = Z.groups.map(function (g) { var q = px(g.lon, g.lat); return { x: q[0], y: q[1], r: Math.max(9, Math.sqrt(g.mw) * kscale) / 2 + 3 }; });
    var placed = [], out = [];
    GEO.zone.features.filter(function (f) { return f.properties.zone && f.properties.name !== 'Berlin'; }).forEach(function (f) {
      var name = f.properties.name, text = LAND_LABEL[name] || NAME_ES[name] || name, lines = text.split('\n');
      var w = Math.max.apply(null, lines.map(function (l) { return l.length; })) * 6.4 + 6, h = lines.length * 14 + 4;
      var cand = [], lon, lat, sx = 0, sy = 0;
      for (lon = ZONE_VIEW[0]; lon <= ZONE_VIEW[2]; lon += 0.06) for (lat = ZONE_VIEW[1]; lat <= ZONE_VIEW[3]; lat += 0.045) {
        if (inFeature(lon, lat, f)) { var q = px(lon, lat); cand.push({ lon: lon, lat: lat, x: q[0], y: q[1] }); sx += q[0]; sy += q[1]; }
      }
      if (!cand.length) return;
      sx /= cand.length; sy /= cand.length;
      var best = null, bs = -1e9;
      cand.forEach(function (c) {
        var clear = 1e9;
        circles.forEach(function (k) { var dx = Math.max(Math.abs(c.x - k.x) - w / 2, 0), dy = Math.max(Math.abs(c.y - k.y) - h / 2, 0); clear = Math.min(clear, Math.hypot(dx, dy) - k.r); });
        placed.forEach(function (r) { var dx = Math.max(Math.abs(c.x - r.x) - (w + r.w) / 2, 0), dy = Math.max(Math.abs(c.y - r.y) - (h + r.h) / 2, 0); clear = Math.min(clear, Math.max(dx, dy)); });
        var score = Math.min(clear, 4) * 3 - Math.hypot(c.x - sx, c.y - sy) * 0.05;
        if (score > bs) { bs = score; best = c; }
      });
      placed.push({ x: best.x, y: best.y, w: w, h: h });
      out.push({ name: text, value: [best.lon, best.lat] });
    });
    return out;
  }
  function mapOption(c) {
    var m = mapPalette(), dom = Z.domain, mob = isMobile();
    var maxMw = Math.max.apply(null, Z.groups.map(function (g) { return g.mw; }));
    var k = (mob ? 38 : 54) / Math.sqrt(maxMw);
    var land = Z.turbines.filter(function (t) { return !t[3]; }), sea = Z.turbines.filter(function (t) { return t[3]; });
    var regions = GEO.zone.features.map(function (f) {
      var p = f.properties, zone = p.zone, ctry = p.kind === 'country';
      return {
        name: NAME_ES[p.name] || p.name, itemStyle: { areaColor: zone ? m.zone : ctry ? m.country : m.land, borderColor: zone ? m.zoneBorder : m.border, borderWidth: zone ? 1.1 : 0.7 },
        label: { show: !mob && ctry, color: m.cl, fontSize: 10.5, fontFamily: FONT, fontStyle: 'italic', formatter: function (o) { return NAME_ES[o.name] || o.name; } }
      };
    });
    var lines = [];
    var nl = dom.n_lat, no = dom.n_lon, i;
    for (i = 0; i < nl; i++) { var la = dom.lat_min + i * dom.step; lines.push({ coords: [[dom.lon_min, la], [dom.lon_max, la]] }); }
    for (i = 0; i < no; i++) { var lo = dom.lon_min + i * dom.step; lines.push({ coords: [[lo, dom.lat_min], [lo, dom.lat_max]] }); }
    var series = [];
    if (mapState.grid) {
      series.push({ type: 'lines', coordinateSystem: 'geo', silent: true, z: 2, polyline: false, data: lines, lineStyle: { color: alpha(c.wind, isDark() ? 0.34 : 0.3), width: 0.6 }, animation: false });
    }
    series.push({ type: 'lines', coordinateSystem: 'geo', silent: true, z: 2, polyline: true, animation: false,
      data: [{ coords: [[dom.lon_min, dom.lat_min], [dom.lon_max, dom.lat_min], [dom.lon_max, dom.lat_max], [dom.lon_min, dom.lat_max], [dom.lon_min, dom.lat_min]] }],
      lineStyle: { color: c.coral, width: 1.8, type: 'dashed' } });
    var visual = null;
    if (mapState.mode === 'points') {
      var sym = function (v) { return 2.2 + Math.min(5.5, Math.sqrt(v[2]) * 0.5); };
      series.push({ type: 'scatter', coordinateSystem: 'geo', data: land, symbolSize: sym, silent: true, z: 3, progressive: 0, itemStyle: { color: alpha(c.wind, 0.72) }, animation: false });
      series.push({ type: 'scatter', coordinateSystem: 'geo', data: sea, symbolSize: sym, silent: true, z: 3, progressive: 0, itemStyle: { color: alpha(c.sea, 0.85) }, animation: false });
    } else {
      var vals = Z.turbines.map(function (t) { return t[2]; }).sort(function (a, b) { return a - b; });
      visual = { show: false, min: 0, max: vals[Math.floor(vals.length * 0.97)] || 1, seriesIndex: series.length,
        inRange: { color: isDark() ? ['rgba(76,196,204,0)', 'rgba(76,196,204,.55)', '#eeb455', '#ff8f78'] : ['rgba(11,122,131,0)', 'rgba(11,122,131,.5)', '#e8a33d', '#cf5339'] } };
      series.push({ type: 'heatmap', coordinateSystem: 'geo', data: Z.turbines.map(function (t) { return [t[0], t[1], t[2]]; }), progressive: 0, pointSize: mob ? 6 : 8, blurSize: mob ? 9 : 12, silent: true, z: 3 });
    }
    series.push({
      name: 'Grupos', type: 'scatter', coordinateSystem: 'geo', z: 6,
      data: Z.groups.map(function (g) {
        var s = g.id === sel, col = g.kind === 'sea' ? c.sea : c.ink;
        return { name: g.label, id: g.id, value: [g.lon, g.lat, g.mw], n: g.n, itemStyle: { color: alpha(s ? c.coral : col, s ? 0.26 : 0.14), borderColor: s ? c.coral : col, borderWidth: s ? 3 : 1.5 } };
      }),
      symbolSize: function (v) { return Math.max(9, Math.sqrt(v[2]) * k); },
      emphasis: { scale: 1.08, itemStyle: { borderWidth: 2.5 } },
      label: { show: false },
      tooltip: { formatter: function (p) { return '<b>' + esc(p.name) + '</b><br>' + num(p.value[2]) + ' MW · ' + num(p.data.n) + ' turbinas'; } }
    });
    if (!mob) {
      var W = (document.getElementById('map') || { clientWidth: 600 }).clientWidth;
      series.push({ type: 'scatter', coordinateSystem: 'geo', silent: true, z: 5, symbolSize: 0, animation: false, data: placeStateLabels(W, k),
        label: { show: true, formatter: '{b}', position: 'inside', color: m.label, fontFamily: FONT, fontSize: 11.5, fontWeight: 500, lineHeight: 14, textBorderColor: m.zone, textBorderWidth: 3 } });
    }
    series.push({ type: 'scatter', coordinateSystem: 'geo', silent: true, z: 7, symbolSize: 0, animation: false,
      data: [{ value: [dom.lon_min, dom.lat_max] }],
      label: { show: true, position: 'right', offset: [4, -8], color: c.coral, fontFamily: FONT, fontSize: 11, fontWeight: 600, formatter: 'Zona de la previsión meteorológica' } });
    var opt = Object.assign(base(c), {
      backgroundColor: m.sea,
      geo: geoBox('zone', ZONE_VIEW, { nameMap: NAME_ES, regions: regions, itemStyle: { areaColor: m.land, borderColor: m.border } }),
      series: series
    });
    if (visual) opt.visualMap = visual;
    opt.tooltip = Object.assign({}, opt.tooltip, { trigger: 'item' });
    return opt;
  }
  function growthOption(c) {
    var g = groupById(sel), col = g.kind === 'sea' ? c.sea : c.wind;
    return Object.assign(base(c), {
      grid: { left: 44, right: 10, top: 10, bottom: 24 },
      xAxis: Object.assign({ type: 'time', min: pd('2015-01-01') }, axisBase(c), { splitLine: { show: false }, axisLabel: { color: c.muted, fontSize: 11, hideOverlap: true, formatter: tfmt } }),
      yAxis: Object.assign({ type: 'value', splitNumber: 3 }, axisBase(c), { axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: c.muted, fontSize: 11, formatter: function (v) { return num(v); } } }),
      tooltip: Object.assign({}, base(c).tooltip, { trigger: 'axis', formatter: function (p) { return monthYear(p[0].value[0]) + '<br><b>' + num(p[0].value[1]) + ' MW</b>'; } }),
      series: [{ type: 'line', step: 'end', showSymbol: false, data: g.growth.map(function (d) { return [pd(d[0]), d[1]]; }), lineStyle: { color: col, width: 2 }, areaStyle: { color: alpha(col, 0.16) } }]
    });
  }
  function renderSide() {
    var el = $('#side');
    if (!Z) { empty(el, 'Sin datos del registro de aerogeneradores.'); return; }
    var g = groupById(sel), first = g.growth.length ? g.growth[0][1] : null;
    var mx = Z.groups[0].mw;
    var pct = first ? Math.round((g.mw / first - 1) * 100) : null;
    el.innerHTML = '<div><div class="gd-kicker">Grupo seleccionado</div><h3 class="gd-name">' + esc(g.name) + '</h3><div class="gd-sub">' + esc(g.kind === 'sea' ? 'Parque marino · ' + (g.label !== g.name ? g.label : 'Mar Báltico') : (STATE_ES[g.state] || g.state) + ' · en tierra') + '</div>' +
      '<div class="gd-stats"><div><b>' + num(g.mw) + '</b><span>MW de potencia</span></div><div><b>' + num(g.n) + '</b><span>aerogeneradores</span></div><div><b>' + (g.hub_m ? num(g.hub_m) + ' m' : '—') + '</b><span>altura media del buje</span></div></div>' +
      '<div class="gd-growth chart" id="gdGrowth"></div>' +
      '<p class="gd-note">' + (pct != null ? 'Potencia del grupo desde 2015: ' + (pct >= 0 ? '+' : '') + num(pct) + ' % (de ' + num(first) + ' a ' + num(g.mw) + ' MW).' : '') + '</p></div>' +
      '<div class="glist" role="list">' + Z.groups.map(function (x) {
        return '<button type="button" class="grow ' + x.kind + '" data-id="' + x.id + '" aria-current="' + (x.id === sel) + '"><span>' + esc(x.label) + '</span><span class="mw">' + num(x.mw) + ' MW</span><span class="bar"><i style="width:' + (x.mw / mx * 100) + '%"></i></span></button>';
      }).join('') + '</div>';
    mount('gdGrowth', growthOption);
    $$('.grow', el).forEach(function (b) { b.addEventListener('click', function () { select(b.getAttribute('data-id')); }); });
  }
  function select(id) {
    sel = id;
    var keep = $('.glist') ? $('.glist').scrollTop : 0;
    renderSide();
    if ($('.glist')) $('.glist').scrollTop = keep;
    draw('map', false);
  }
  if (!Z) { empty($('#map'), 'Sin datos del registro de aerogeneradores.'); }
  else {
    fitHeight('map', ZONE_VIEW);
    mount('map', mapOption, { after: function (chart) { chart.on('click', { seriesName: 'Grupos' }, function (p) { select(p.data.id); }); } });
    renderSide();
    $('#mapLegend').innerHTML = '<span><i class="sw dot" style="background:var(--wind)"></i>Aerogeneradores en tierra</span><span><i class="sw dot" style="background:var(--sea)"></i>Aerogeneradores en el mar</span><span><i class="sw dot" style="border:1.5px solid var(--ink);background:transparent"></i>Grupo (tamaño = potencia)</span><span><i class="sw" style="border:1.5px dashed var(--coral);background:transparent"></i>Zona de la previsión meteorológica</span>';
    $$('#mapMode button').forEach(function (b) { b.addEventListener('click', function () {
      mapState.mode = b.getAttribute('data-m');
      $$('#mapMode button').forEach(function (x) { x.setAttribute('aria-pressed', String(x === b)); });
      draw('map', false);
    }); });
    $('#mapGrid').addEventListener('change', function (e) { mapState.grid = e.target.checked; draw('map', false); });
  }

  /* ============ Capítulo 3: las fuentes ============ */
  var SRC_COLOR = { nwp: 'wind', entsoe: 'amber', mastr: 'sea' };
  var WORLD_VIEW = [-128, 30, 24, 60], EU_VIEW = [-5.6, 40.9, 11.6, 53.4];
  function renderWorld() {
    var S = D.sources || {}, places = S.places || [], routes = S.routes || [], el = $('#world');
    if (!places.length) { empty(el, 'Sin descripción de las fuentes.'); $('#worldEu').style.display = 'none'; return; }
    var mob = isMobile(), pl = {}, num_ = {};
    places.forEach(function (p, i) { pl[p.id] = p; num_[p.id] = i + 1; });
    fitHeight('world', WORLD_VIEW); fitHeight('worldEu', EU_VIEW);
    function look(c) { var dk = isDark(); return { sea: dk ? '#0a1a2c' : '#e7eef3', land: dk ? '#172b43' : '#f5f1e8', border: dk ? '#2c4562' : '#c6bfae' }; }
    function pts(c, ids, size) {
      return { type: 'scatter', coordinateSystem: 'geo', z: 6, silent: true, symbolSize: size,
        data: ids.map(function (id) { var p = pl[id]; return { name: p.name, value: [p.lon, p.lat], id: id }; }),
        itemStyle: { color: c.ink, borderColor: c.paper, borderWidth: 2 },
        label: { show: true, formatter: function (o) { return String(num_[o.data.id]); }, color: c.paper, fontFamily: FONT, fontSize: size > 20 ? 12 : 10, fontWeight: 700, position: 'inside' }
      };
    }
    function names(c, ids, pos) {
      return { type: 'scatter', coordinateSystem: 'geo', z: 7, silent: true, symbolSize: 0,
        data: ids.map(function (id) { var p = pl[id]; return { value: [p.lon, p.lat], name: p.name.replace(', ', '\n').replace(' (', '\n('), label: { position: pos[id] || 'top' } }; }),
        label: { show: true, formatter: '{b}', color: c.ink, fontFamily: FONT, fontSize: 12, fontWeight: 600, lineHeight: 15, textBorderColor: look(c).sea, textBorderWidth: 3, distance: 15 } };
    }
    function route(c, r, curv) {
      var a = pl[r.from], b = pl[r.to];
      return { coords: [[a.lon, a.lat], [b.lon, b.lat]], lineStyle: { color: c[SRC_COLOR[r.source] || 'wind'], curveness: curv } };
    }
    function arcs(c, list, curv) {
      return { type: 'lines', coordinateSystem: 'geo', z: 3, silent: true, data: list, symbol: ['none', 'arrow'], symbolSize: 9, lineStyle: { width: 2.2, opacity: 0.95 }, effect: { show: ANIM, period: 6, trailLength: 0.25, symbolSize: 4, color: '#fff' }, animation: false };
    }
    mount('world', function (c) {
      var l = look(c), rs = routes.filter(function (r) { return pl[r.from] && pl[r.to]; });
      var box = [[EU_VIEW[0], EU_VIEW[1]], [EU_VIEW[2], EU_VIEW[1]], [EU_VIEW[2], EU_VIEW[3]], [EU_VIEW[0], EU_VIEW[3]], [EU_VIEW[0], EU_VIEW[1]]];
      return Object.assign(base(c), {
        backgroundColor: l.sea,
        geo: geoBox('world', WORLD_VIEW, { itemStyle: { areaColor: l.land, borderColor: l.border, borderWidth: 0.6 } }),
        series: [
          { type: 'lines', coordinateSystem: 'geo', z: 2, silent: true, polyline: true, animation: false, data: [{ coords: box }], lineStyle: { color: c.ink, width: 1.3, type: 'dashed', opacity: 0.8 } },
          arcs(c, rs.filter(function (r) { return r.source === 'nwp'; }).map(function (r) { return route(c, r, r.from === 'ecmwf' ? -0.25 : 0.25); })),
          pts(c, ['dynamical'], 22), names(c, ['dynamical'], { dynamical: 'right' }),
          pts(c, places.filter(function (p) { return p.id !== 'dynamical'; }).map(function (p) { return p.id; }), 14)
        ]
      });
    });
    mount('worldEu', function (c) {
      var l = look(c), rs = routes.filter(function (r) { return pl[r.from] && pl[r.to] && r.source !== 'nwp'; });
      var inEu = places.filter(function (p) { return p.lon >= EU_VIEW[0] && p.lon <= EU_VIEW[2] && p.lat >= EU_VIEW[1] && p.lat <= EU_VIEW[3]; }).map(function (p) { return p.id; });
      var lab = { ecmwf: 'top', entsoe: 'top', bnetza: 'bottom', nas: 'bottom' };
      var nas = pl.nas, stub = nas ? { coords: [[EU_VIEW[0] + 0.25, nas.lat + 2.4], [nas.lon, nas.lat]], lineStyle: { color: c.wind, curveness: 0.15 } } : null;
      return Object.assign(base(c), {
        backgroundColor: l.sea,
        geo: geoBox('world', EU_VIEW, { itemStyle: { areaColor: l.land, borderColor: l.border, borderWidth: 0.7 } }),
        series: [
          arcs(c, rs.map(function (r) { return route(c, r, r.from === 'entsoe' ? 0.2 : -0.18); }).concat(stub ? [stub] : [])),
          { type: 'scatter', coordinateSystem: 'geo', silent: true, symbolSize: 0, z: 5, data: stub ? [{ value: stub.coords[0], name: 'Desde Oregón' }] : [],
            label: { show: true, formatter: '{b}', position: 'right', color: c.wind, fontFamily: FONT, fontSize: 11.5, fontWeight: 600, textBorderColor: l.sea, textBorderWidth: 3, distance: 6 } },
          pts(c, inEu, 24), names(c, inEu, lab)
        ]
      });
    });
    $('#worldList').innerHTML = places.map(function (p, i) { return '<li><span class="num">' + (i + 1) + '</span><div><b>' + esc(p.name) + '</b>' + esc(p.role || '') + '</div></li>'; }).join('');
    $('#worldLegend').innerHTML = '<span><i class="sw" style="background:var(--wind)"></i>Previsión meteorológica</span><span><i class="sw" style="background:var(--amber)"></i>Producción, precios y operador</span><span><i class="sw" style="background:var(--sea)"></i>Registro de aerogeneradores</span>';
  }
  function renderSourceCards() {
    var S = D.sources || {}, cards = S.cards || [], el = $('#sourceCards');
    if (!cards.length) { empty(el, 'Sin descripción de las fuentes.'); return; }
    var V = D.meta.versions || {}, d = PR.daily;
    var vol = function (id) {
      if (id === 'nwp') { var b = VOL.nwp && VOL.nwp.bronze; return (b != null ? bytesTxt(b) + ' en nuestro almacén' : 'Almacén no disponible ahora') + (nwpRow ? ' · ' + num(nwpRow.n_ok) + ' días' : ''); }
      if (id === 'entsoe') { var e = VOL.entsoe && VOL.entsoe.bronze; return (e != null ? bytesTxt(e) + ' en bruto' : 'no disponible') + (d ? ' · ' + num(d.n_days) + ' días de producción' : ''); }
      var m = VOL.mastr && VOL.mastr.bronze; return (m != null ? bytesTxt(m) + ' el archivo original' : 'Archivo original no disponible ahora') + (Z ? ' · ' + num(Z.n_turbines) + ' turbinas activas en la zona' : '');
    };
    var upd = function (id) {
      if (id === 'nwp') return V.ifs_range && V.ifs_range[1] ? 'Pasada del ' + dateLong(V.ifs_range[1]) : null;
      if (id === 'entsoe') return V.entsoe_last_fetch ? dateLong(V.entsoe_last_fetch) : null;
      return V.mastr_export ? 'Exportación del ' + dateLong(V.mastr_export) : null;
    };
    el.innerHTML = cards.map(function (s) {
      var u = upd(s.id);
      return '<article class="card src-card"><span class="chip" style="align-self:flex-start;background:' + alpha(C()[SRC_COLOR[s.id] || 'wind'], 0.16) + '">' + esc(s.id === 'nwp' ? 'Meteorología' : s.id === 'entsoe' ? 'Producción' : 'Parques') + '</span><h3>' + esc(s.name) + '</h3><div class="prov">' + esc(s.provider) + '</div><p>' + esc(s.gives) + '</p>' +
        '<dl class="facts"><div><dt>Frecuencia</dt><dd>' + esc(s.frequency) + '</dd></div><div><dt>Desde</dt><dd>' + esc(s.since) + '</dd></div><div><dt>Volumen</dt><dd>' + esc(vol(s.id)) + '</dd></div>' + (u ? '<div><dt>Última carga</dt><dd>' + esc(u) + '</dd></div>' : '') + '<div><dt>Licencia</dt><dd>' + esc(s.license) + '</dd></div></dl></article>';
    }).join('');
  }
  renderWorld(); renderSourceCards();

  /* ============ Capítulo 4: la previsión por dentro ============ */
  var F = D.forecast, FB = F && F.bronze, FV = D.variables || { groups: {}, cards: [] };
  var ICON = {
    turbine: '<path d="M12 12V22M9 22h6"/><circle cx="12" cy="11" r="1.4"/><path d="M12 9.6V3M10.8 11.7L5 15M13.2 11.7L19 15"/>',
    windlow: '<path d="M3 9h10a2.5 2.5 0 1 0-2.5-2.5M3 14h14a2.5 2.5 0 1 1-2.5 2.5M3 19h6"/>',
    arrow: '<path d="M4 12h15M14 7l5 5-5 5"/>',
    gust: '<path d="M3 8h8l2-3 2 6 2-3h4M3 15h6l2-3 2 5 2-2h6"/>',
    thermo: '<path d="M10 14V5a2 2 0 1 1 4 0v9a4 4 0 1 1-4 0z"/><path d="M12 9v8"/>',
    gauge: '<path d="M4 17a8 8 0 1 1 16 0"/><path d="M12 17l4-5"/><circle cx="12" cy="17" r="1"/>',
    layers: '<path d="M12 4l9 4-9 4-9-4z"/><path d="M3 12l9 4 9-4M3 16l9 4 9-4"/>',
    isobar: '<path d="M3 8c4-4 14-4 18 0M3 13c4-4 14-4 18 0M3 18c4-4 14-4 18 0"/>'
  };
  function icon(k) { return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (ICON[k] || ICON.isobar) + '</svg>'; }
  function ramp(t) {
    var st = isDark() ? [[18, 48, 74], [28, 106, 122], [63, 176, 184], [159, 224, 224], [243, 241, 220]] : [[232, 243, 243], [159, 208, 211], [63, 154, 163], [11, 102, 112], [18, 54, 90]];
    t = Math.max(0, Math.min(1, t)); var x = t * (st.length - 1), i = Math.min(st.length - 2, Math.floor(x)), f = x - i;
    return 'rgb(' + st[i].map(function (v, k) { return Math.round(v + (st[i + 1][k] - v) * f); }).join(',') + ')';
  }
  function renderFNumbers() {
    var el = $('#fNumbers');
    if (!F) { empty(el, 'Sin información de la ingesta.'); return; }
    var v = VOL.nwp || {}, mbDay = v.bronze && v.passes ? v.bronze / v.passes : null, kbDay = v.silver && v.passes ? v.silver / v.passes : null;
    var nv = F.variables.length, nd = F.derived.length;
    var items = [
      [num(nv), 'variables meteorológicas' + (nd ? ' (más ' + nd + ' velocidades calculadas)' : '')],
      [num(F.leads.length), 'instantes por pasada, de +' + F.leads[0] + ' h a +' + F.leads[F.leads.length - 1] + ' h'],
      [num(F.n_members), 'escenarios de cada campo'],
      [Z ? num(Z.domain.n_lat * Z.domain.n_lon) : '—', Z ? 'celdas de ' + num(Z.domain.step, 2) + '° (' + Z.domain.n_lon + ' × ' + Z.domain.n_lat + ')' : 'celdas de la malla'],
      [Z ? num(Z.n_groups) : '—', 'puntos de parques (grupos)'],
      [mbDay != null ? bytes(mbDay).v + ' ' + bytes(mbDay).u : '—', 'por día guardado en bruto' + (kbDay != null ? ' (' + bytesTxt(kbDay) + ' ya resumido)' : '')]
    ];
    el.innerHTML = items.map(function (i) { return '<div><b class="tn">' + i[0] + '</b><span>' + esc(i[1]) + '</span></div>'; }).join('');
  }
  function renderFVars() {
    var el = $('#fVars'), cards = FV.cards || [];
    if (!cards.length) { empty(el, 'Sin descripción de las variables.'); return; }
    var order = Object.keys(FV.groups || {}), html = '';
    order.forEach(function (g) {
      var list = cards.filter(function (c) { return c.group === g; });
      if (!list.length) return;
      html += '<div class="fv-group">' + esc(FV.groups[g]) + '</div><div class="fv-grid">' + list.map(function (c) {
        var tags = (c.derived ? '<span class="chip teal">Calculada</span>' : '') + (c.id === 'wind_gust_10m' && F && F.gust_since ? '<span class="chip warn">Solo desde el ' + esc(dateLong(F.gust_since)) + '</span>' : '');
        return '<article class="fv"><div class="fv-ic">' + icon(c.icon) + '</div><div><div class="fv-n">' + esc(c.name) + '</div><div class="fv-u">' + (c.unit ? esc(c.unit) : '') + tags + '</div><p>' + esc(c.why) + '</p></div></article>';
      }).join('') + '</div>';
    });
    el.innerHTML = html;
  }

  /* malla */
  var gState = { step: 0 };
  var GRID_VIEW = [8.2, 49.2, 16.3, 56.3];
  function gridCells() {
    var cells = [], lats = FB ? FB.lats : null, lons = FB ? FB.lons : null, d = Z.domain, i, j;
    if (!lats) { lats = []; lons = []; for (i = 0; i < d.n_lat; i++) lats.push(d.lat_min + i * d.step); for (j = 0; j < d.n_lon; j++) lons.push(d.lon_min + j * d.step); }
    var vals = FB ? FB.steps[gState.step] : null;
    for (i = 0; i < lats.length; i++) for (j = 0; j < lons.length; j++) cells.push([lons[j], lats[i], vals ? vals[i * lons.length + j] : null, j, i]);
    return cells;
  }
  function gridOption(c) {
    var m = mapPalette(), d = Z.domain, h = d.step / 2, vmax = FB ? FB.vmax : 1, cells = gridCells(), mob = isMobile();
    return Object.assign(base(c), {
      backgroundColor: m.sea,
      geo: geoBox('zone', GRID_VIEW, { itemStyle: { areaColor: m.land, borderColor: m.border, borderWidth: 0.7 } }),
      tooltip: Object.assign({}, base(c).tooltip, { trigger: 'item' }),
      series: borderLines('zone', GRID_VIEW, c).concat([
        { name: 'Celdas', type: 'custom', coordinateSystem: 'geo', z: 3, progressive: 0, data: cells, animation: false,
          renderItem: function (p, api) {
            var lon = api.value(0), lat = api.value(1), v = api.value(2);
            var a = api.coord([lon - h, lat + h]), b = api.coord([lon + h, lat - h]);
            return { type: 'rect', shape: { x: a[0], y: a[1], width: b[0] - a[0], height: b[1] - a[1] }, style: { fill: v == null ? 'rgba(0,0,0,0)' : ramp(v / vmax), opacity: v == null ? 1 : 0.82, stroke: alpha(c.wind, isDark() ? 0.45 : 0.4), lineWidth: 0.5 } };
          },
          tooltip: { formatter: function (p) {
            var lat = p.value[1], w = d.step * 111.32 * Math.cos(lat * Math.PI / 180), hh = d.step * 111.2;
            return '<b>' + num(lat, 2) + '° N, ' + num(p.value[0], 2) + '° E</b><br>Celda ' + (p.value[3] + 1) + ' de ' + d.n_lon + ' (este-oeste), ' + (p.value[4] + 1) + ' de ' + d.n_lat + ' (norte-sur)<br>Tamaño aproximado: ' + num(w) + ' × ' + num(hh) + ' km' +
              (p.value[2] != null ? '<br>Viento medio a 100 m: <b>' + num(p.value[2], 1) + ' m/s</b>' : '');
          } } },
        { name: 'Grupos', type: 'scatter', coordinateSystem: 'geo', z: 6, symbolSize: mob ? 7 : 9, data: Z.groups.map(function (g) { return { name: g.label, value: [g.lon, g.lat] }; }),
          itemStyle: { color: c.paper, borderColor: c.ink, borderWidth: 2 }, tooltip: { formatter: function (p) { return '<b>' + esc(p.name) + '</b><br>Punto de la previsión (grupo de parques)'; } } }
      ])
    });
  }
  function leadLabel(i) {
    var init = pd(FB.init), t = init + F.leads[i] * 3600000, d = new Date(t);
    var wd = ['dom', 'lun', 'mar', 'mié', 'jue', 'vie', 'sáb'][d.getUTCDay()];
    return wd + ' ' + d.getUTCDate() + ' ' + MON3[d.getUTCMonth()] + ', ' + ('0' + d.getUTCHours()).slice(-2) + ':00 UTC';
  }
  function renderFGrid() {
    var el = $('#fGrid');
    if (!Z) { empty(el, 'Sin datos de la zona.'); return; }
    fitHeight('fGrid', GRID_VIEW);
    mount('fGrid', gridOption);
    var d = Z.domain, midlat = (d.lat_min + d.lat_max) / 2;
    $('#fGridFacts').innerHTML = [
      ['Zona', num(d.lat_min, 1) + '–' + num(d.lat_max, 1) + '° N · ' + num(d.lon_min, 1) + '–' + num(d.lon_max, 1) + '° E'],
      ['Celdas', num(d.n_lon) + ' × ' + num(d.n_lat) + ' = ' + num(d.n_lon * d.n_lat)],
      ['Tamaño de celda', '≈ ' + num(d.step * 111.32 * Math.cos(midlat * Math.PI / 180)) + ' km (este-oeste) × ' + num(d.step * 111.2) + ' km (norte-sur)'],
      ['Puntos de parques', num(Z.n_groups) + ' grupos, interpolados entre las 4 celdas vecinas']
    ].map(function (r) { return '<div><dt>' + esc(r[0]) + '</dt><dd>' + esc(r[1]) + '</dd></div>'; }).join('');
    var st = $('#fStepper');
    if (!FB) {
      st.innerHTML = '';
      $('#fGridNote').textContent = 'El almacén de la previsión no está disponible ahora mismo, así que se muestra solo la malla. Cuando lo esté, aquí aparecerá el viento de un día real.';
      return;
    }
    $('#fGridNote').textContent = 'Campo real: media de los ' + F.n_members + ' escenarios de la pasada del ' + dateLong(FB.init) + ' a las 00:00 UTC, tal como está guardada en el almacén.';
    st.innerHTML = '<label><span>Instante de la previsión</span><b id="fLead"></b></label><input type="range" id="fRange" min="0" max="' + (F.leads.length - 1) + '" step="1" value="' + gState.step + '" aria-label="Instante de la previsión">' +
      '<div class="ramp" style="background:linear-gradient(90deg,' + [0, 0.25, 0.5, 0.75, 1].map(function (t) { return ramp(t); }).join(',') + ')"></div><div class="ramp-l"><span>0 m/s</span><span>viento medio a 100 m</span><span>' + num(FB.vmax) + ' m/s</span></div>';
    function upd() { $('#fLead').textContent = '+' + F.leads[gState.step] + ' h · ' + leadLabel(gState.step); }
    upd();
    $('#fRange').addEventListener('input', function (e) { gState.step = +e.target.value; upd(); draw('fGrid', false); });
  }

  /* plazos */
  var leadSeason = CK ? (CK.summer_now ? 'summer' : 'winter') : 'summer';
  function renderFLeads() {
    var el = $('#fLeads');
    if (!F) { empty(el, 'Sin información de la ingesta.'); return; }
    var H = 60, pos = function (h) { return h / H * 100; }, off = leadSeason === 'summer' ? 2 : 1;
    var w0 = 24 - off, w1 = w0 + 24, close = CK ? (leadSeason === 'summer' ? CK.close_summer : CK.close_winter) : null;
    var cl = close ? +close.split(':')[0] + (+close.split(':')[1]) / 60 : null, arr = CK ? CK.arrival_median_s / 3600 : null;
    var l0 = F.leads[0], l1 = F.leads[F.leads.length - 1], h, html = '<div class="ck-axis"><div class="ck-line"></div>';
    for (h = 0; h <= H; h += 6) html += '<div class="ck-tick" style="--a:' + pos(h) + '"><span>' + ('0' + (h % 24)).slice(-2) + ':00</span></div>';
    [0, 24, 48].forEach(function (h2, i) { html += '<div class="ck-day" style="--a:' + pos(h2) + '"><span>' + ['Hoy (pasada)', 'Mañana', 'Pasado mañana'][i] + '</span></div>'; });
    html += '<div class="ck-band arr" style="--a:' + pos(w0) + ';--b:' + pos(w1) + '"></div>';
    F.leads.forEach(function (l) { html += '<div class="ck-dot" style="--a:' + pos(l) + '"></div><div class="ck-sl" style="--a:' + pos(l) + '">+' + l + '</div>'; });
    var notes = [];
    if (cl != null) { html += '<div class="ck-mark" style="--a:' + pos(cl) + '"></div>'; notes.push(['dn close', pos(cl), close + ' UTC', 'Cierre del plazo', 'Hay que enviar antes de esta hora.']); }
    if (arr != null) notes.push(['up', pos(arr), hmm(arr) + ' UTC', 'Llega la pasada', 'Con ella empieza el día de trabajo.']);
    notes.push(['dn margin', pos((w0 + w1) / 2), ('0' + w0).slice(-2) + ':00 → ' + ('0' + (w1 - 24)).slice(-2) + ':00 UTC', 'El día de la liga', 'Las 24 horas del día siguiente en Madrid: es lo que hay que pronosticar.']);
    notes.push(['up', pos((l0 + l1) / 2 + 3), '+' + l0 + ' h a +' + l1 + ' h · ' + F.leads.length + ' pasos', 'Lo que usamos de la pasada', 'Cubre el día de la liga con ' + (w0 - l0) + ' h de margen al principio y ' + (l1 - w1) + ' h al final.']);
    notes.forEach(function (n) { html += '<div class="ck-lab c ' + n[0] + '" style="--a:' + n[1] + '"><span class="t">' + esc(n[2]) + '</span><b>' + esc(n[3]) + '</b>' + esc(n[4]) + '</div>'; });
    html += '</div><div class="fl-notes">' + notes.map(function (n) { return '<div><span class="t">' + esc(n[2]) + '</span><b>' + esc(n[3]) + '</b>' + esc(n[4]) + '</div>'; }).join('') + '</div>';
    el.innerHTML = html;
    $$('#fSeason button').forEach(function (b) { b.setAttribute('aria-pressed', String(b.getAttribute('data-s') === leadSeason)); });
  }
  $$('#fSeason button').forEach(function (b) { b.addEventListener('click', function () { leadSeason = b.getAttribute('data-s'); renderFLeads(); }); });

  /* escenarios */
  var mState = { mode: 'all' };
  function membersOption(c) {
    var M = FB.members, t = F.leads.map(function (l) { return pd(FB.init) + l * 3600000; }), S = M.stats, mode = mState.mode;
    var showM = mode !== 'stats', showS = mode !== 'members', series = [];
    if (showM) M.series.forEach(function (m, i) { series.push({ name: 'Escenario ' + (i + 1), type: 'line', data: m.map(function (v, k) { return [t[k], v]; }), symbol: 'none', silent: true, lineStyle: { color: c.pending, width: 1, opacity: mode === 'all' ? 0.55 : 0.8 }, z: 1 }); });
    if (showS) {
      series.push({ name: 'banda base', type: 'line', stack: 'b', data: t.map(function (x, k) { return [x, S.p10[k]]; }), symbol: 'none', lineStyle: { opacity: 0 }, areaStyle: { opacity: 0 }, silent: true, z: 2 });
      series.push({ name: 'banda', type: 'line', stack: 'b', data: t.map(function (x, k) { return [x, +(S.p90[k] - S.p10[k]).toFixed(2)]; }), symbol: 'none', lineStyle: { opacity: 0 }, areaStyle: { color: alpha(c.wind, 0.2) }, silent: true, z: 2 });
      series.push({ name: 'Mediana (p50)', type: 'line', data: t.map(function (x, k) { return [x, S.p50[k]]; }), symbol: 'circle', symbolSize: 5, lineStyle: { color: c.wind, width: 2.6 }, itemStyle: { color: c.wind }, z: 4 });
      series.push({ name: 'Media', type: 'line', data: t.map(function (x, k) { return [x, S.mean[k]]; }), symbol: 'none', lineStyle: { color: c.ink, width: 1.6, type: 'dashed' }, z: 3 });
      series.push({ name: 'Control', type: 'line', data: t.map(function (x, k) { return [x, S.ctrl[k]]; }), symbol: 'none', lineStyle: { color: c.amber, width: 1.8 }, z: 3 });
    }
    return Object.assign(base(c), {
      grid: { left: 44, right: 22, top: 32, bottom: 34 },
      xAxis: Object.assign({ type: 'time', min: t[0], max: t[t.length - 1], splitNumber: mob() ? 3 : 6 }, axisBase(c), { splitLine: { show: false }, axisLabel: { color: c.muted, fontSize: 11, hideOverlap: true, formatter: function (v) { var d = new Date(v); return d.getUTCHours() === 0 ? d.getUTCDate() + ' ' + MON3[d.getUTCMonth()] : ('0' + d.getUTCHours()).slice(-2) + ':00'; } } }),
      yAxis: Object.assign({ type: 'value', min: 0, name: 'm/s', nameTextStyle: { color: c.muted, fontSize: 11, align: 'right' } }, axisBase(c), { axisLine: { show: false }, axisTick: { show: false } }),
      tooltip: Object.assign({}, base(c).tooltip, { trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: c.line2 } }, formatter: function (ps) {
        var k = ps[0].dataIndex, col = M.series.map(function (m) { return m[k]; });
        return '<b>' + dmy(t[k]) + ' ' + hm(t[k]) + ' UTC</b> (+' + F.leads[k] + ' h)<br>Escenarios: de ' + num(Math.min.apply(null, col), 1) + ' a ' + num(Math.max.apply(null, col), 1) + ' m/s<br>p10 ' + num(S.p10[k], 1) + ' · <b>p50 ' + num(S.p50[k], 1) + '</b> · p90 ' + num(S.p90[k], 1) +
          '<br>Media ' + num(S.mean[k], 1) + ' · control ' + num(S.ctrl[k], 1) + ' · dispersión ' + num(S.std[k], 1) + ' m/s';
      } }),
      series: series
    });
  }
  function mob() { return isMobile(); }
  function renderFMembers() {
    var el = $('#fMembers');
    if (!FB || !FB.members) { empty(el, 'El almacén de la previsión no está disponible ahora mismo; no se pueden dibujar los escenarios.'); $('#fMode').style.display = 'none'; return; }
    $('#fMembersTitle').textContent = 'Viento a 100 m en ' + FB.members.group.name + ' · pasada del ' + dateShort(FB.init);
    mount('fMembers', membersOption);
    $('#fMembersLegend').innerHTML = '<span><i class="sw" style="background:var(--pending)"></i>Cada uno de los ' + F.n_members + ' escenarios</span><span><i class="sw" style="background:var(--wind)"></i>Mediana (p50)</span><span><i class="sw" style="background:var(--wind);opacity:.3"></i>Entre p10 y p90</span><span><i class="sw" style="background:var(--ink)"></i>Media</span><span><i class="sw" style="background:var(--amber)"></i>Control</span><span>La dispersión se lee en la ventana emergente</span>';
    $$('#fMode button').forEach(function (b) { b.addEventListener('click', function () { mState.mode = b.getAttribute('data-m'); $$('#fMode button').forEach(function (x) { x.setAttribute('aria-pressed', String(x === b)); }); draw('fMembers', false); }); });
  }

  /* viento previsto frente a producción */
  function vsOption(c) {
    var V = F.vs, t = V.days.map(pd), n = t.length, start = t[Math.max(0, n - 100)], end = t[n - 1];
    return Object.assign(base(c), {
      grid: { left: 52, right: 64, top: 34, bottom: 72 },
      xAxis: Object.assign({ type: 'time', splitNumber: mob() ? 3 : 6 }, axisBase(c), { splitLine: { show: false }, axisLabel: { color: c.muted, fontSize: 11.5, formatter: tfmt, hideOverlap: true } }),
      yAxis: [
        Object.assign({ type: 'value', min: 0, name: mob() ? 'm/s' : 'Viento previsto (m/s)', nameTextStyle: { color: c.wind, fontSize: 11, align: 'left' } }, axisBase(c), { axisLine: { show: false }, axisTick: { show: false } }),
        Object.assign({ type: 'value', min: 0, name: mob() ? 'MW' : 'Producción real (MW)', nameTextStyle: { color: c.amber, fontSize: 11, align: 'right' }, splitLine: { show: false } }, axisBase(c), { splitLine: { show: false }, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: c.muted, fontSize: 11.5, formatter: function (v) { return num(v); } } })
      ],
      dataZoom: [{ type: 'inside', filterMode: 'none', startValue: start, endValue: end }, { type: 'slider', filterMode: 'none', startValue: start, endValue: end, height: 22, bottom: 12, borderColor: c.line, backgroundColor: 'transparent', fillerColor: alpha(c.wind, 0.14), handleStyle: { color: c.wind, borderColor: c.wind }, textStyle: { color: c.muted, fontSize: 11 }, dataBackground: { lineStyle: { color: c.line2 }, areaStyle: { color: alpha(c.wind, 0.08) } } }],
      tooltip: Object.assign({}, base(c).tooltip, { trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: c.line2 } }, formatter: function (ps) {
        var k = ps[0].dataIndex;
        return '<b>' + dmy(t[k]) + '</b><br><span style="color:' + c.wind + '">●</span> Viento previsto: <b>' + num(V.p50[k], 1) + ' m/s</b> (rango ' + num(V.p10[k], 1) + '–' + num(V.p90[k], 1) + ')<br><span style="color:' + c.amber + '">●</span> Producción real: ' + (V.prod[k] == null ? 'aún no disponible' : '<b>' + num(V.prod[k]) + ' MW</b>');
      } }),
      series: [
        { name: 'base', type: 'line', stack: 'v', data: t.map(function (x, k) { return [x, V.p10[k]]; }), symbol: 'none', lineStyle: { opacity: 0 }, areaStyle: { opacity: 0 }, silent: true, z: 1 },
        { name: 'banda', type: 'line', stack: 'v', data: t.map(function (x, k) { return [x, +(V.p90[k] - V.p10[k]).toFixed(2)]; }), symbol: 'none', lineStyle: { opacity: 0 }, areaStyle: { color: alpha(c.wind, 0.2) }, silent: true, z: 1 },
        { name: 'Viento previsto', type: 'line', data: t.map(function (x, k) { return [x, V.p50[k]]; }), symbol: 'none', lineStyle: { color: c.wind, width: 2 }, z: 3 },
        { name: 'Producción', type: 'line', yAxisIndex: 1, data: t.map(function (x, k) { return [x, V.prod[k]]; }), symbol: 'none', connectNulls: false, lineStyle: { color: c.amber, width: 2 }, areaStyle: { color: alpha(c.amber, 0.1) }, z: 2 }
      ]
    });
  }
  function renderFVs() {
    var el = $('#fVs'), V = F && F.vs;
    if (!V) { empty(el, 'Sin previsión guardada o sin producción real para compararlas.'); $('#fVsText').textContent = ''; return; }
    mount('fVs', vsOption);
    $('#fVsLegend').innerHTML = '<span><i class="sw" style="background:var(--wind)"></i>Viento previsto a 100 m (mediana)</span><span><i class="sw" style="background:var(--wind);opacity:.3"></i>Rango del 80 %</span><span><i class="sw" style="background:var(--amber)"></i>Producción real en tierra (media del día, MW)</span><span>Viento: media ponderada por potencia de los grupos en tierra, para el día siguiente a cada pasada</span>';
    $('#fVsText').innerHTML = 'Cada punto compara lo que la pasada de medianoche decía del viento del día siguiente con lo que luego produjeron los parques en tierra. ' + (V.corr != null ? 'En los ' + num(V.n_days) + ' días guardados la correlación entre ambas es de <b>' + num(V.corr, 2) + '</b> (1 sería perfecta): cuando se prevé más viento, se produce más. ' : '') +
      'No es una relación de uno a uno: la producción crece con el cubo del viento y se satura cuando las turbinas llegan a su potencia máxima, y de eso se encargará el modelo.';
  }
  function renderPorDentro() {
    var ideaEl = $('#idea3b');
    if (F && Z) {
      var v = VOL.nwp || {}, mb = v.bronze && v.passes ? bytesTxt(v.bronze / v.passes) : null;
      ideaEl.innerHTML = 'De cada pasada usamos <b>' + F.variables.length + '</b> variables, <b>' + F.leads.length + '</b> instantes, <b>' + F.n_members + '</b> escenarios y <b>' + num(Z.domain.n_lat * Z.domain.n_lon) + '</b> celdas' + (mb ? ': <b>' + mb + '</b> por día guardado.' : '.');
    } else ideaEl.textContent = 'Qué campos del modelo europeo usamos y cómo se convierten en un resumen por parque.';
    renderFNumbers(); renderFVars(); renderFGrid(); renderFLeads(); renderFMembers(); renderFVs();
  }
  renderPorDentro();

  /* ============ Capítulo 4: el viaje del dato ============ */
  function renderJourney() {
    var J = D.journey || {}, el = $('#journey');
    if (!J.lanes || !J.lanes.length) { empty(el, 'Sin descripción del recorrido de los datos.'); return; }
    var cols = J.columns || [], vals = [];
    J.lanes.forEach(function (l) { var v = VOL[l.id] || {}; ['download', 'bronze', 'silver'].forEach(function (k) { if (v[k]) vals.push(v[k]); }); });
    var lo = Math.log10(Math.min.apply(null, vals.concat([1e9]))), hi = Math.log10(Math.max.apply(null, vals.concat([1e3])));
    var wid = function (b) { return hi > lo ? 6 + 94 * (Math.log10(b) - lo) / (hi - lo) : 50; };
    var h = cols.map(function (c) { return '<div class="jr-h">' + esc(c.title) + '</div>'; }).join('');
    J.lanes.forEach(function (l) {
      var v = VOL[l.id] || {};
      ['origin', 'download', 'bronze', 'silver'].forEach(function (k, i) {
        var title = (cols.filter(function (c) { return c.key === k; })[0] || {}).title || k;
        var b = v[k], vol = '';
        if (k !== 'origin') {
          var f = bytes(b);
          vol = f ? '<div class="vol tn">' + f.v + '<small>' + f.u + '</small></div><div class="lg"><i style="width:' + wid(b) + '%"></i></div>' : '<div class="vol" style="font-size:16px;color:var(--muted)">no disponible ahora</div>';
        }
        h += '<div class="jr-cell o-' + l.id + (k === 'silver' ? ' last' : '') + (k !== 'origin' && b == null ? ' na' : '') + '" data-col="' + esc(title) + '">' +
          (k === 'origin' ? '<div class="jr-lane">' + esc(l.name) + '</div>' : '') + vol + '<p>' + esc((l.steps || {})[k] || '') + '</p></div>';
      });
    });
    var nx = J.next || {};
    ['features', 'model'].forEach(function (k, i) {
      var n = nx[k] || {};
      h += '<div class="jr-next c' + (5 + i) + '"><span class="chip">' + esc(n.chip || 'Próximamente') + '</span><h4>' + esc(n.title || k) + '</h4><p>' + esc(n.text || '') + '</p></div>';
    });
    el.innerHTML = h;
    var sum = J.lanes.map(function (l) {
      var v = VOL[l.id] || {};
      if (!v.download || !v.silver) return '';
      var r = v.download / v.silver, less = r >= 1, k = less ? r : 1 / r;
      return '<div><b class="tn">×' + (k >= 10 ? num(Math.round(k)) : num(k, 1)) + '</b>' + (less ? 'menos' : 'más') + ' volumen en «' + esc(l.name.toLowerCase()) + '» (de ' + esc(bytesTxt(v.download)) + ' a ' + esc(bytesTxt(v.silver)) + (less ? '' : '; la tabla ordenada ocupa más que la respuesta comprimida') + ')</div>';
    }).join('');
    $('#journeySum').innerHTML = sum;
    if (!sum) $('#journeySum').style.display = 'none';
  }
  renderJourney();

  /* ============ Capítulo 5: calidad ============ */
  var QCOL = function (c) { return { 1: c.ok, 2: c.amber, 3: c.coral, 4: alpha(c.pending, 0.5) }; };
  var QTXT = { 1: 'Completo', 2: 'Rellenado o parcial', 3: 'Hueco', 4: 'Pendiente de descargar' };
  function runs(codes) {
    var out = [], i = 0;
    while (i < codes.length) { var c = codes[i], j = i; while (j + 1 < codes.length && codes[j + 1] === c) j++; if (c !== '0') out.push([i, j, +c]); i = j + 1; }
    return out;
  }
  function calOption(c) {
    var t0 = pd(QL.start), n = QL.n_days, mob = isMobile();
    var data = [];
    QL.rows.forEach(function (r, y) { runs(r.codes).forEach(function (u) { data.push([y, t0 + u[0] * DAY, t0 + (u[1] + 1) * DAY, u[2]]); }); });
    var cols = QCOL(c);
    return Object.assign(base(c), {
      grid: { left: mob ? 92 : 214, right: 14, top: 8, bottom: 58 },
      xAxis: Object.assign({ type: 'time', min: t0, max: t0 + n * DAY }, axisBase(c), { splitLine: { show: true, lineStyle: { color: c.line } }, axisLabel: { color: c.muted, fontSize: 11.5, formatter: tfmt, hideOverlap: true } }),
      yAxis: Object.assign({ type: 'category', inverse: true, data: QL.rows.map(function (r) { return r.label; }) }, axisBase(c), { axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false },
        axisLabel: { color: c.ink2, fontSize: mob ? 11 : 13, width: mob ? 84 : 200, overflow: 'break', lineHeight: 15 } }),
      dataZoom: [{ type: 'inside', filterMode: 'none', xAxisIndex: 0 }, { type: 'slider', xAxisIndex: 0, filterMode: 'none', height: 22, bottom: 8, borderColor: c.line, backgroundColor: 'transparent', fillerColor: alpha(c.wind, 0.14), handleStyle: { color: c.wind, borderColor: c.wind }, textStyle: { color: c.muted, fontSize: 11 }, dataBackground: { lineStyle: { color: c.line }, areaStyle: { color: c.line } } }],
      tooltip: Object.assign({}, base(c).tooltip, { trigger: 'item', formatter: function (p) {
        var v = p.value, days = Math.round((v[2] - v[1]) / DAY);
        return '<b>' + esc(QL.rows[v[0]].label) + '</b><br>' + QTXT[v[3]] + '<br><span style="color:' + c.muted + '">' + (days > 1 ? dmy(v[1]) + ' – ' + dmy(v[2] - DAY) + ' · ' + num(days) + ' días' : dmy(v[1])) + '</span>';
      } }),
      series: [{ type: 'custom', clip: true, encode: { x: [1, 2], y: 0 }, data: data, renderItem: function (params, api) {
        var y = api.value(0), s = api.coord([api.value(1), y]), e = api.coord([api.value(2), y]), h = api.size([0, 1])[1] * 0.58;
        var x0 = Math.max(s[0], params.coordSys.x), x1 = Math.min(e[0], params.coordSys.x + params.coordSys.width);
        return { type: 'rect', shape: { x: x0, y: s[1] - h / 2, width: Math.max(x1 - x0, 1.8), height: h, r: 2 }, style: { fill: cols[api.value(3)] } };
      } }]
    });
  }
  function calZoom(r) {
    var ch = reg.qCal && reg.qCal.inst; if (!ch) return;
    var t0 = pd(QL.start), end = t0 + QL.n_days * DAY, start = t0;
    if (r === '2024') start = pd('2024-03-15'); else if (r === '13m') start = end - 13 * 30.44 * DAY;
    ch.dispatchAction({ type: 'dataZoom', startValue: start, endValue: end });
  }
  function renderQuality() {
    if (!QL) { empty($('#qCal'), 'Sin informes de validación.'); return; }
    var cov = QL.coverage_days, gaps = (QL.gaps || []).filter(function (g) { return g.level !== 'info'; });
    var pct = QL.checks_total ? QL.checks_ok / QL.checks_total * 100 : 0;
    $('#qStats').innerHTML =
      '<div class="card stat"><div class="l">Controles superados</div><div class="v tn">' + QL.checks_ok + '<small>de ' + QL.checks_total + '</small></div><div class="meter"><i style="width:' + pct + '%"></i></div><div class="l">Formato, huecos, cambios entre descargas y rangos físicos.</div></div>' +
      '<div class="card stat"><div class="l">Previsión meteorológica completa</div><div class="v tn">' + (QL.coverage != null ? num(QL.coverage * 100) : '—') + '<small>%</small></div><div class="meter"><i style="width:' + (QL.coverage || 0) * 100 + '%"></i></div><div class="l">' + (cov ? num(cov[0]) + ' de ' + num(cov[1]) + ' días entre el ' + dateShort(QL.coverage_range[0]) + ' y el ' + dateShort(QL.coverage_range[1]) + '. La puerta exige el 98 %.' : 'Sin informe.') + '</div></div>' +
      '<div class="card stat"><div class="l">Avisos abiertos</div><div class="v tn">' + gaps.length + '</div><div class="meter"><i style="width:' + Math.min(100, gaps.length * 20) + '%;background:var(--amber)"></i></div><div class="l">Tramos que faltan o conviene saber; ninguno impide entrenar.</div></div>';
    $('#qLegend').innerHTML = [1, 2, 3, 4].map(function (k) { return '<span><i class="sw" style="background:' + QCOL(C())[k] + '"></i>' + QTXT[k] + '</span>'; }).join('') + '<span>Arrastre o use la rueda para ampliar</span>';
    mount('qCal', calOption, { after: function () { calZoom(currentQ); } });
    $('#qGaps').innerHTML = (QL.gaps || []).length ? QL.gaps.map(function (g) { return '<div class="gap-item ' + g.level + '"><b>' + esc(g.title) + '</b><span>' + esc(g.text) + '</span></div>'; }).join('') : '<div class="empty">Sin huecos ni avisos.</div>';
    var chk = QL.checks || [], bySrc = {};
    chk.forEach(function (x) { (bySrc[x.source] = bySrc[x.source] || []).push(x); });
    var okSvg = '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 6.4l2.3 2.3 4.7-5"/></svg>';
    var warnSvg = '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 2.5v4.2M6 9.2v.1"/></svg>';
    $('#qChecks').innerHTML = '<div class="card-title">Controles automáticos</div><details open><summary>' + QL.checks_ok + ' de ' + QL.checks_total + ' superados</summary><ul>' + chk.map(function (x) {
      return '<li><span class="mk ' + (x.ok ? '' : x.warn ? 'warn' : 'bad') + '">' + (x.ok ? okSvg : warnSvg) + '</span><em>' + esc(x.source) + '</em><span>' + esc(x.check) + '</span></li>';
    }).join('') + '</ul></details>';
  }
  var currentQ = '2024';
  renderQuality();
  $$('#qRange button').forEach(function (b) { b.addEventListener('click', function () {
    currentQ = b.getAttribute('data-r');
    $$('#qRange button').forEach(function (x) { x.setAttribute('aria-pressed', String(x === b)); });
    calZoom(currentQ);
  }); });

  /* ============ Capítulo 6: viento y producción ============ */
  var pState = { kind: 'on', range: 'all', hidden: {}, day: null };
  function dailyOption(c) {
    var d = PR.daily, t0 = pd(d.start), on = pState.kind === 'on';
    var col = on ? c.wind : c.sea, prod = on ? d.onshore : d.offshore, cap = on ? d.cap_onshore : d.cap_offshore;
    var P1 = prod.map(function (v, i) { return [t0 + i * DAY, v]; });
    var C1 = cap ? cap.map(function (v, i) { return [t0 + i * DAY, v]; }) : [];
    var end = t0 + (prod.length - 1) * DAY, start = t0;
    if (pState.range === '3y') start = end - 3 * 365.25 * DAY; else if (pState.range === '1y') start = end - 365.25 * DAY;
    return Object.assign(base(c), {
      grid: { left: 62, right: 18, top: 38, bottom: 74 },
      xAxis: Object.assign({ type: 'time' }, axisBase(c), { splitLine: { show: false }, axisLabel: { color: c.muted, fontSize: 11.5, formatter: tfmt, hideOverlap: true } }),
      yAxis: Object.assign({ type: 'value', name: 'MW', nameTextStyle: { color: c.muted, fontSize: 11, align: 'right' }, nameGap: 10 }, axisBase(c), { axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: c.muted, fontSize: 11.5, formatter: function (v) { return num(v); } } }),
      dataZoom: [{ type: 'inside', filterMode: 'none', startValue: start, endValue: end }, { type: 'slider', filterMode: 'none', startValue: start, endValue: end, height: 24, bottom: 14, borderColor: c.line, backgroundColor: 'transparent', fillerColor: alpha(col, 0.14), handleStyle: { color: col, borderColor: col }, textStyle: { color: c.muted, fontSize: 11 }, dataBackground: { lineStyle: { color: c.line2 }, areaStyle: { color: alpha(col, 0.1) } } }],
      tooltip: Object.assign({}, base(c).tooltip, { trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: c.line2 } }, formatter: function (ps) {
        var t = ps[0].value[0], pv = null, cv2 = null;
        ps.forEach(function (p) { if (p.seriesIndex === 0) pv = p.value[1]; else cv2 = p.value[1]; });
        var s = '<b>' + dmy(t) + '</b><br><span style="color:' + col + '">●</span> Producción media: <b>' + num(pv) + ' MW</b>';
        if (cv2) s += '<br><span style="color:' + c.muted + '">- -</span> Potencia instalada: ' + num(cv2) + ' MW<br>Funcionó al <b>' + num(pv / cv2 * 100) + ' %</b> de su potencia';
        return s;
      } }),
      series: [
        { name: 'Producción', type: 'line', data: P1, showSymbol: false, sampling: 'lttb', lineStyle: { color: col, width: 1.2 }, areaStyle: { color: alpha(col, 0.2) }, z: 2 },
        { name: 'Potencia instalada', type: 'line', data: C1, step: 'end', showSymbol: false, lineStyle: { color: c.ink2, width: 1.5, type: 'dashed' }, z: 3 }
      ]
    });
  }
  function stackColors(c) {
    var hues = { Brandenburg: [11, 122, 131], 'Mecklenburg-Vorpommern': [118, 96, 190], Sachsen: [208, 120, 74], 'Sachsen-Anhalt': [214, 164, 46], 'Thüringen': [96, 160, 88], Ostsee: [42, 111, 176] };
    var seen = {}, out = {};
    PR.stack.series.forEach(function (s) {
      var i = seen[s.state] = (seen[s.state] == null ? 0 : seen[s.state] + 1), base0 = hues[s.state] || [120, 120, 120];
      out[s.id] = mix(base0, isDark() ? [20, 30, 45] : [255, 255, 255], [0, 0.28, 0.5][Math.min(i, 2)] * (isDark() ? 0.7 : 1));
    });
    out.__hues = hues;
    return out;
  }
  function stackOption(c) {
    var S = PR.stack, cols = stackColors(c);
    var months = S.months.map(pd);
    return Object.assign(base(c), {
      color: S.series.map(function (s) { return cols[s.id]; }),
      grid: { left: 62, right: 18, top: 34, bottom: 36 },
      legend: { show: false, data: S.series.map(function (s) { return s.name; }) },
      xAxis: Object.assign({ type: 'time' }, axisBase(c), { splitLine: { show: false }, axisLabel: { color: c.muted, fontSize: 11.5, formatter: tfmt, hideOverlap: true } }),
      yAxis: Object.assign({ type: 'value', name: 'MW', nameTextStyle: { color: c.muted, fontSize: 11, align: 'right' }, nameGap: 10 }, axisBase(c), { axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: c.muted, fontSize: 11.5, formatter: function (v) { return num(v); } } }),
      tooltip: Object.assign({}, base(c).tooltip, { trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: c.line2 } }, formatter: function (ps) {
        var by = {}, total = 0;
        ps.forEach(function (p) { var st = S.series[p.seriesIndex].state; if (!pState.hidden[st]) { by[st] = (by[st] || 0) + p.value[1]; total += p.value[1]; } });
        var s = '<b>' + monthYear(ps[0].value[0]) + '</b>';
        Object.keys(by).sort(function (a, b) { return by[b] - by[a]; }).forEach(function (st) {
          var h = cols.__hues[st] || [120, 120, 120];
          s += '<br><span style="color:rgb(' + h.join(',') + ')">●</span> ' + esc(STATE_ES[st] || st) + ': <b>' + num(by[st]) + ' MW</b>';
        });
        return s + '<br>Total: <b>' + num(total) + ' MW</b>';
      } }),
      series: S.series.map(function (s, i) {
        var hid = !!pState.hidden[s.state];
        return { name: s.name, type: 'line', stack: 'total', symbol: 'none', smooth: false, areaStyle: { opacity: hid ? 0 : 0.92 }, lineStyle: { width: 0.6, color: c.card, opacity: hid ? 0 : 1 },
          emphasis: { disabled: true }, data: months.map(function (m, k) { return [m, hid ? 0 : s.data[k]]; }) };
      })
    });
  }
  function exOption(group, ymax) {
    return function (c) {
      var day = PR.example.days[pState.day], g = day.groups[group.id];
      var t = day.valid.map(function (v) { return Date.parse(v); });
      var col = group.kind === 'sea' ? c.sea : c.wind;
      var band = t.map(function (x, i) { return [x, +(g.p90[i] - g.p10[i]).toFixed(2)]; });
      return Object.assign(base(c), {
        grid: { left: 40, right: 24, top: 12, bottom: 30 },
        xAxis: Object.assign({ type: 'time', min: t[0], max: t[t.length - 1], splitNumber: 3 }, axisBase(c), { splitLine: { show: false }, axisLabel: { color: c.muted, fontSize: 11, hideOverlap: true, formatter: function (v) { var d = new Date(v); return d.getUTCHours() === 0 ? d.getUTCDate() + ' ' + MON3[d.getUTCMonth()] : ('0' + d.getUTCHours()).slice(-2) + ':00'; } } }),
        yAxis: Object.assign({ type: 'value', min: 0, max: ymax, splitNumber: 4 }, axisBase(c), { axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: c.muted, fontSize: 11, formatter: function (v) { return num(v); } } }),
        tooltip: Object.assign({}, base(c).tooltip, { trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: c.line2 } }, formatter: function (ps) {
          var i = ps[0].dataIndex;
          return '<b>' + dmy(t[i]) + ' ' + hm(t[i]) + ' UTC</b><br>Escenario central: <b>' + num(g.p50[i], 1) + ' m/s</b><br>Rango del 80 %: ' + num(g.p10[i], 1) + ' a ' + num(g.p90[i], 1) + ' m/s<br><span style="color:' + c.muted + '">Media de los escenarios: ' + num(g.mean[i], 1) + ' m/s</span>';
        } }),
        series: [
          { type: 'line', stack: 'b', data: t.map(function (x, i) { return [x, g.p10[i]]; }), symbol: 'none', lineStyle: { opacity: 0 }, areaStyle: { opacity: 0 }, silent: true },
          { type: 'line', stack: 'b', data: band, symbol: 'none', lineStyle: { opacity: 0 }, areaStyle: { color: alpha(col, 0.24) }, silent: true },
          { type: 'line', data: t.map(function (x, i) { return [x, g.p50[i]]; }), smooth: 0.25, symbol: 'circle', symbolSize: 5, lineStyle: { color: col, width: 2.4 }, itemStyle: { color: col } }
        ]
      });
    };
  }
  function renderExample() {
    var E = PR.example, el = $('#pExample');
    if (!E || !E.days.length) { empty(el, 'No hay previsión meteorológica guardada para mostrar un ejemplo.'); $('#pDays').innerHTML = ''; return; }
    if (pState.day == null) pState.day = E.days.length - 1;
    $('#pDays').innerHTML = E.days.map(function (d, i) { return '<button type="button" data-i="' + i + '" aria-pressed="' + (i === pState.day) + '">' + dateShort(d.init) + '</button>'; }).join('');
    $$('#pDays button').forEach(function (b) { b.addEventListener('click', function () { pState.day = +b.getAttribute('data-i'); renderExampleCharts(); $$('#pDays button').forEach(function (x) { x.setAttribute('aria-pressed', String(x === b)); }); }); });
    el.innerHTML = E.groups.map(function (g, i) { return '<div><div class="ex-t">' + esc(g.name) + '</div><div class="ex-s">' + (g.kind === 'sea' ? 'Mar Báltico' : 'En tierra') + '</div><div class="chart" id="pEx' + i + '"></div></div>'; }).join('');
    $('#pExLegend').innerHTML = '<span><i class="sw" style="background:var(--wind)"></i>Escenario central (mediana de los ' + D.meta.n_members + ' escenarios)</span><span><i class="sw" style="background:var(--wind);opacity:.3"></i>Rango del 80 %: de 10 a 90 de cada 100 escenarios</span><span>Eje: velocidad del viento a 100 m (m/s), hora UTC</span>';
    renderExampleCharts();
  }
  function renderExampleCharts() {
    var E = PR.example, day = E.days[pState.day], ymax = 0;
    E.groups.forEach(function (g) { var x = day.groups[g.id]; if (x) ymax = Math.max(ymax, Math.max.apply(null, x.p90)); });
    ymax = Math.ceil(ymax / 2) * 2;
    E.groups.forEach(function (g, i) { if (day.groups[g.id]) mount('pEx' + i, exOption(g, ymax)); });
  }
  function renderProduction() {
    var d = PR.daily;
    if (!d) { empty($('#pDaily'), 'Sin datos de producción real.'); } else {
      mount('pDaily', dailyOption);
      $('#pDailyLegend').innerHTML = '<span><i class="sw" style="background:var(--wind)"></i><span id="pLegKind">Producción media diaria</span></span><span><i class="sw" style="border-top:2px dashed var(--ink-2);height:0;border-radius:0"></i>Potencia instalada ese día</span>';
      $$('#pKind button').forEach(function (b) { b.addEventListener('click', function () { pState.kind = b.getAttribute('data-k'); $$('#pKind button').forEach(function (x) { x.setAttribute('aria-pressed', String(x === b)); }); draw('pDaily', false); }); });
      $$('#pRange button').forEach(function (b) { b.addEventListener('click', function () { pState.range = b.getAttribute('data-r'); $$('#pRange button').forEach(function (x) { x.setAttribute('aria-pressed', String(x === b)); }); draw('pDaily', false); }); });
    }
    var S = PR.stack;
    if (!S) { empty($('#pStack'), 'Sin datos de potencia instalada.'); } else {
      mount('pStack', stackOption);
      var cols = stackColors(C()), states = [];
      S.series.forEach(function (s) { if (states.indexOf(s.state) < 0) states.push(s.state); });
      $('#pStackLegend').innerHTML = states.map(function (st) { var h = cols.__hues[st] || [120, 120, 120]; return '<button type="button" data-st="' + esc(st) + '" aria-pressed="true"><i class="sw dot" style="background:rgb(' + h.join(',') + ')"></i>' + esc(STATE_ES[st] || st) + '</button>'; }).join('');
      $$('#pStackLegend button').forEach(function (b) { b.addEventListener('click', function () {
        var st = b.getAttribute('data-st'); pState.hidden[st] = !pState.hidden[st]; b.setAttribute('aria-pressed', String(!pState.hidden[st])); draw('pStack', false);
      }); });
    }
    renderExample();
  }
  renderProduction();

  /* ============ Capítulo 7: el reloj ============ */
  var ckSeason = CK ? (CK.summer_now ? 'summer' : 'winter') : 'summer';
  function clockInfo(season) {
    var closeStr = season === 'summer' ? CK.close_summer : CK.close_winter;
    var close = +closeStr.split(':')[0] + (+closeStr.split(':')[1]) / 60;
    var arrMax = CK.arrival_max_s / 3600;
    return { close: close, closeStr: closeStr, off: season === 'summer' ? 2 : 1, marginMin: Math.round((close - arrMax) * 60) };
  }
  function hmm(h) { var m = Math.round(h * 60); return ('0' + Math.floor(m / 60)).slice(-2) + ':' + ('0' + (m % 60)).slice(-2); }
  function dur(min) { return Math.floor(min / 60) + ' h ' + ('0' + (min % 60)).slice(-2) + ' min'; }
  function renderClock() {
    var el = $('#clock');
    if (!CK) { empty(el, 'Aún no se ha medido la hora de llegada de la previsión.'); $('#ckNotes').innerHTML = ''; return; }
    var h0 = 7, h1 = 16, pos = function (h) { return (h - h0) / (h1 - h0) * 100; };
    var ci = clockInfo(ckSeason), arr0 = CK.arrival_min_s / 3600, arr1 = CK.arrival_max_s / 3600, med = CK.arrival_median_s / 3600;
    var html = '<div class="ck-axis"><div class="ck-line"></div>', h;
    for (h = h0; h <= h1; h++) html += '<div class="ck-tick" style="--a:' + pos(h) + '"><span>' + hmm(h) + '</span><span class="mad">' + hmm(h + ci.off) + '</span></div>';
    html += '<div class="ck-band margin" style="--a:' + pos(arr1) + ';--b:' + pos(ci.close) + '"></div>';
    html += '<div class="ck-band arr" style="--a:' + pos(arr0) + ';--b:' + pos(arr1) + '"></div>';
    html += '<div class="ck-band after" style="--a:' + pos(ci.close) + ';--b:' + pos(h1) + '"></div>';
    html += '<div class="ck-mark" style="--a:' + pos(ci.close) + '"></div>';
    html += '<div class="ck-lab c up arrl" style="--a:' + pos(med) + '"><span class="t">' + hmm(arr0) + '–' + hmm(arr1) + ' UTC · mediana ' + hmm(med) + '</span><b>Llega la previsión meteorológica</b>Medido durante ' + num(CK.n_days) + ' días, del ' + dateShort(CK.first_day) + ' al ' + dateShort(CK.last_day) + '. En Madrid: ' + hmm(med + ci.off) + '.</div>';
    html += '<div class="ck-lab c dn margin" style="--a:' + pos((arr1 + ci.close) / 2) + '"><span class="t">Margen de ' + dur(ci.marginMin) + '</span><b>Tiempo para trabajar</b>Procesar la previsión, calcular el pronóstico, enviarlo y reintentar si algo falla.</div>';
    html += '<div class="ck-lab c up close" style="--a:' + pos(ci.close) + '"><span class="t">' + ci.closeStr + ' UTC · ' + hmm(ci.close + ci.off) + ' Madrid</span><b>Cierre de la liga</b>Último momento para enviar el pronóstico del día siguiente.</div>';
    html += '<div class="ck-lab c dn" style="--a:' + pos((ci.close + h1) / 2) + '"><span class="t">Después del cierre</span><b>Publica el operador</b>' + esc((D.clock_notes || {}).operator_note || 'El operador publica su previsión cuando ya no podemos usarla.') + '</div>';
    html += '</div>';
    el.innerHTML = html;
    $$('#ckSeason button').forEach(function (b) { b.setAttribute('aria-pressed', String(b.getAttribute('data-s') === ckSeason)); });
    var other = ckSeason === 'summer' ? 'winter' : 'summer', oi = clockInfo(other);
    var chg = CK.next_change ? dateLong(CK.next_change) : null;
    $('#ckNotes').innerHTML =
      '<div><b class="tn">' + dur(ci.marginMin) + '</b>de margen entre la llegada más tardía medida y el cierre. Exigimos al menos ' + num(CK.margin_required_min || 60) + ' minutos.</div>' +
      '<div><b class="tn">' + (CK.pass_seconds != null ? '≈ ' + num(CK.pass_seconds) + ' s' : '—') + '</b>en descargar, recortar y resumir una pasada completa (mediana de las ya procesadas).</div>' +
      '<div><b class="tn">' + (chg ? dateShort(CK.next_change) : '—') + '</b>' + (chg ? 'cambio de hora: el cierre pasa de las ' + ci.closeStr + ' a las ' + oi.closeStr + ' UTC (siempre las 15:00 en Madrid).' : 'sin cambio de hora previsto en el próximo año.') + '</div>';
  }
  $$('#ckSeason button').forEach(function (b) { b.addEventListener('click', function () { ckSeason = b.getAttribute('data-s'); renderClock(); }); });
  renderClock();

  /* ============ Capítulo 8: riesgos ============ */
  (function () {
    var I = D.insights || [], el = $('#insights');
    if (!I.length) { empty(el, 'Sin hallazgos registrados.'); return; }
    var tag = { risk: ['bad', 'Riesgo'], watch: ['warn', 'Vigilar'], ok: ['ok', 'Resuelto'], info: ['teal', 'Contexto'] };
    var order = { risk: 0, watch: 1, info: 2, ok: 3 };
    el.innerHTML = I.slice().sort(function (a, b) { return (order[a.level] || 9) - (order[b.level] || 9); }).map(function (i) {
      var t = tag[i.level] || ['', 'Nota'];
      return '<article class="card insight ' + esc(i.level) + '"><span class="chip ' + t[0] + '" style="align-self:flex-start">' + t[1] + '</span><h3>' + esc(i.title) + '</h3><dl>' +
        '<div><dt>Qué vemos</dt><dd>' + esc(i.what) + '</dd></div><div><dt>Por qué importa</dt><dd>' + esc(i.meaning) + '</dd></div><div><dt>Qué hacemos</dt><dd>' + esc(i.action) + '</dd></div></dl></article>';
    }).join('');
  })();

  /* ============ Capítulo 9: el modelo ============ */
  (function () {
    var M = D.model || {}, el = $('#model');
    var dd = M.version_date ? Math.round((pd(M.version_date) - pd(D.meta.today)) / DAY) : null;
    var gates = P ? P.gates : [];
    el.innerHTML = '<span class="chip warn">Próximamente · ' + esc(M.version_label || 'v0.1') + (M.version_date ? ', ' + esc(dateShort(M.version_date)) : '') + '</span>' +
      '<h3>Todavía no hay modelo, pero ya sabemos cómo lo vamos a medir</h3>' +
      '<p class="lead">El modelo devolverá, para cada cuarto de hora del día siguiente, tres valores: uno bajo, uno central y uno alto (' + gl('cuantil', 'cuantiles') + ' 10, 50 y 90).' + (dd != null && dd >= 0 ? ' Su primera versión llega en <b>' + dd + '</b> días.' : '') + ' Estas son las medidas con las que se juzgará:</p>' +
      '<div class="grid-2">' + (M.metrics || []).map(function (m) { return '<div class="m"><h4>' + esc(m.title) + '</h4><p>' + esc(m.text) + (m.gl ? ' ' + gl(m.gl, '¿Qué es?') : '') + '</p></div>'; }).join('') + '</div>' +
      (gates.length ? '<div class="gates">' + gates.map(function (g) {
        return '<div class="gate"><div class="gid">' + esc(g.gate) + '</div><div class="gd">' + esc(dateShort(g.date)) + ' · ' + ({ done: 'superada', progress: 'en marcha', pending: 'por superar' })[g.status] + '</div><p>' + esc(g.gate_text) + '</p></div>';
      }).join('') + '</div>' : '');
  })();

  /* ============ Capítulo 10: ¿mejora el pronóstico? ============ */
  (function () {
    var BT = D.backtest;
    var elV = $('#btVerdict');
    if (!BT) {
      empty(elV, 'Todavía no hay ninguna prueba del modelo. Se generan con «python -m predico50hz.evaluation run» y «report».');
      ['#btTable', '#btHist', '#btMonthly'].forEach(function (s) { var e = $(s); if (e) e.innerHTML = ''; });
      var cards = $$('#c10 .card'); cards.forEach(function (e) { e.style.display = 'none'; });
      return;
    }
    var H = BT.headline || {}, models = BT.models, thr = (BT.rules || {}).verdict_threshold_pct;
    var cls = { mejora: 'ok', empeora: 'bad', igual: 'warn' }[H.verdict] || '';
    var g1 = H.g1 === 'cumple' ? '<span class="chip ok">Puerta G1: cumplida</span>' : H.g1 === 'no cumple' ? '<span class="chip warn">Puerta G1: sin cumplir</span>' : '';
    var hold = BT.holdout_evaluations_total || 0;
    elV.innerHTML = '<div class="bt-verdict ' + cls + '"><div class="vt">' + (H.text ? esc(H.text) : 'Sin veredicto todavía.') + '</div><div class="vm">' + g1 +
      '<span>Se considera «igual» una diferencia de ±' + num(thr, 0) + ' % en el error.</span>' +
      '<span>' + (hold ? 'El periodo reservado se ha mirado ' + hold + (hold === 1 ? ' vez.' : ' veces.') : 'El periodo reservado aún no se ha mirado.') + '</span></div></div>';

    function sgn(x, d) { if (x == null) return '—'; var v = num(Math.abs(x), d); return (x > 0 ? '+' : x < 0 ? '−' : '') + v; }
    var maxErr = Math.max.apply(null, models.map(function (m) { return m.validation.rmse; }));
    var op = models.filter(function (m) { return m.kind === 'operador'; })[0];
    var opErr = op ? op.validation.rmse : null;
    var rows = models.map(function (m) {
      var v = m.validation, isOp = m.kind === 'operador';
      var vsOp = isOp ? '<span class="muted">referencia</span>' : (v.vs_operator_pct == null ? '—' :
        '<span class="chip ' + ({ mejora: 'ok', empeora: 'bad', igual: 'warn' }[v.verdict] || '') + '">' + esc(v.verdict) + '</span> <span class="tn ' + (v.vs_operator_pct >= 0 ? 'bt-good' : 'bt-bad') + '">' + sgn(v.vs_operator_pct, 1) + ' %</span>');
      var rng = v.winkler == null ? '<span class="muted">no publica rango</span>' : '<span class="val">' + num(v.winkler) + '</span>';
      var cov = v.coverage == null ? '<span class="muted">—</span>' : '<span class="val">' + num(v.coverage * 100, 1) + '<small>%</small></span><div class="sub">' + (v.coverage >= 0.76 && v.coverage <= 0.84 ? '<span class="bt-good">dentro de 76–84 %</span>' : '<span class="bt-bad">fuera de 76–84 %</span>') + '</div>';
      var g = isOp ? '<span class="muted">—</span>' : '<span class="chip ' + (m.g1.status === 'cumple' ? 'ok' : 'warn') + '" title="' + esc(m.g1.reasons.join(' ')) + '">' + (m.g1.status === 'cumple' ? 'cumple' : 'no cumple') + '</span>';
      var tr = m.trend, trend = tr.change_pct == null ? '<span class="muted">primera ejecución</span>' : '<span class="chip ' + ({ mejora: 'ok', empeora: 'bad', igual: 'warn' }[tr.direction] || '') + '">' + esc(tr.direction) + '</span> <span class="tn">' + sgn(tr.change_pct, 1) + ' %</span>';
      var bar = '<div class="bt-bar"><i style="width:' + (100 * v.rmse / maxErr).toFixed(1) + '%"></i>' + (opErr != null ? '<b style="left:' + (100 * opErr / maxErr).toFixed(1) + '%"></b>' : '') + '</div>';
      return '<tr><td><div class="nm">' + esc(m.label) + '</div><div class="sub">' + (isOp ? 'referencia externa' : m.n_runs + (m.n_runs === 1 ? ' ejecución' : ' ejecuciones')) + '</div></td>' +
        '<td><span class="val">' + num(v.rmse) + '<small>MW</small></span>' + bar + '</td><td>' + vsOp + '</td><td>' + rng + '</td><td>' + cov + '</td><td>' + g + '</td><td>' + trend + '</td></tr>';
    }).join('');
    $('#btTable').innerHTML = '<table class="bt-table"><thead><tr><th>Modelo</th><th>' + gl('rmse', 'Error típico') + ' (menos es mejor)</th><th>Frente al operador</th><th>' + gl('winkler', 'Puntuación del rango') + '</th><th>Aciertos del ' + gl('rango80', 'rango del 80 %') + '</th><th>Puerta G1</th><th>Frente a su ejecución anterior</th></tr></thead><tbody>' + rows + '</tbody></table>';
    var p = BT.period || {};
    var notes = '<p>' + gl('backtest', 'Prueba día a día') + ': el modelo predice cada mes sin haberlo visto y solo ha aprendido de los meses anteriores' + (p.validation_start ? ' (de ' + esc(dateShort(p.validation_start)) + ' de ' + p.validation_start.slice(0, 4) + ' a ' + esc(dateShort(p.validation_end)) + ' de ' + p.validation_end.slice(0, 4) + ', ' + p.n_folds + ' meses)' : '') + '. La marca negra de cada barra es el error del operador.</p>';
    var held = models.filter(function (m) { return m.holdout; });
    if (held.length) {
      notes += '<p><b>Periodo reservado</b> (datos que el modelo nunca ha visto ni al elegir sus ajustes): ' + held.map(function (m) {
        var h = m.holdout; return esc(m.label) + ', ' + num(h.rmse) + ' MW' + (m.kind === 'operador' ? '' : ' (' + (h.vs_operator_pct == null ? 'sin comparar' : sgn(h.vs_operator_pct, 1) + ' % frente al operador') + ')') + ' · se ha mirado ' + m.holdout_evaluations + (m.holdout_evaluations === 1 ? ' vez' : ' veces');
      }).join('; ') + '. Es verano y otoño, con poco viento: no garantiza el comportamiento del invierno.</p>';
    }
    $('#btNotes').innerHTML = notes;

    var colorOf = function (c, m, i) { return m.kind === 'operador' ? c.ink : [c.wind, c.amber, c.sea, c.coral, c.ok][i % 5]; };
    var cand = models.filter(function (m) { return m.kind !== 'operador'; });
    function legend(el, c) {
      $(el).innerHTML = models.map(function (m, i) { return '<span><i class="sw dot" style="background:' + colorOf(c, m, cand.indexOf(m)) + '"></i>' + esc(m.label) + '</span>'; }).join('');
    }
    var hist = BT.history || [];
    mount('btHist', function (c) {
      var cats = hist.map(function (h, i) { return 'nº ' + (i + 1); });
      var best = [], cur = null;
      hist.forEach(function (h) { if (h.model !== 'operator' && (cur == null || h.rmse < cur)) cur = h.rmse; best.push(cur); });
      var series = [
        { name: 'Operador', type: 'line', data: hist.map(function (h) { return h.rmse_operator; }), symbol: 'none', lineStyle: { color: c.ink, type: 'dashed', width: 1.8 }, z: 1 },
        { name: 'Mejor hasta la fecha', type: 'line', step: 'end', data: best, symbol: 'none', lineStyle: { color: c.muted, width: 1.4, opacity: .7 }, z: 1 }
      ];
      models.forEach(function (m, i) {
        if (m.kind === 'operador') return;
        series.push({ name: m.label, type: 'scatter', symbolSize: 13, itemStyle: { color: colorOf(c, m, cand.indexOf(m)) }, z: 3,
          data: hist.map(function (h) { return h.model === m.model ? h.rmse : null; }) });
      });
      return Object.assign(base(c), {
        grid: { left: 62, right: 18, top: 24, bottom: 36 },
        xAxis: Object.assign({ type: 'category', data: cats, boundaryGap: true }, axisBase(c), { splitLine: { show: false } }),
        yAxis: Object.assign({ type: 'value', min: 0, name: 'MW', nameTextStyle: { color: c.muted, fontSize: 11, align: 'right' } }, axisBase(c), { axisLine: { show: false }, axisTick: { show: false } }),
        tooltip: Object.assign({}, base(c).tooltip, { trigger: 'axis', formatter: function (ps) {
          var h = hist[ps[0].dataIndex];
          return '<b>' + esc(h.label) + '</b><br>' + esc(String(h.created_at).slice(0, 16).replace('T', ' ')) + ' UTC<br>Error: <b>' + num(h.rmse) + ' MW</b><br>Operador: ' + num(h.rmse_operator) + ' MW';
        } }),
        series: series
      });
    });
    $('#btHistLegend').innerHTML = '<span><i class="sw" style="border-top:2px dashed var(--ink);height:0;width:14px;border-radius:0"></i>Operador</span><span><i class="sw" style="border-top:2px solid var(--muted);height:0;width:14px;border-radius:0"></i>Mejor hasta la fecha</span>';
    var months = {};
    models.forEach(function (m) { m.monthly.forEach(function (r) { months[r.month] = 1; }); });
    var ml = Object.keys(months).sort();
    mount('btMonthly', function (c) {
      return Object.assign(base(c), {
        grid: { left: 62, right: 18, top: 24, bottom: 36 },
        xAxis: Object.assign({ type: 'category', data: ml.map(function (m) { return MON3[+m.slice(5) - 1] + ' ' + m.slice(2, 4); }) }, axisBase(c), { splitLine: { show: false } }),
        yAxis: Object.assign({ type: 'value', min: 0, name: 'MW', nameTextStyle: { color: c.muted, fontSize: 11, align: 'right' } }, axisBase(c), { axisLine: { show: false }, axisTick: { show: false } }),
        tooltip: Object.assign({}, base(c).tooltip, { trigger: 'axis', valueFormatter: function (v) { return v == null ? '—' : num(v) + ' MW'; } }),
        series: models.map(function (m, i) {
          var by = {}; m.monthly.forEach(function (r) { by[r.month] = r.rmse; });
          var col = colorOf(c, m, cand.indexOf(m)), isOp = m.kind === 'operador';
          return { name: m.label, type: 'line', symbol: 'circle', symbolSize: 6, data: ml.map(function (k) { return by[k] == null ? null : by[k]; }),
            lineStyle: { color: col, width: isOp ? 1.8 : 2.2, type: isOp ? 'dashed' : 'solid' }, itemStyle: { color: col } };
        })
      });
    });
    legend('#btMonthlyLegend', C());
    var last = hist.filter(function (h) { return h.model !== 'operator'; });
    var first = last[0], lastRun = last[last.length - 1];
    $('#btReading').textContent = last.length > 1
      ? 'Entre la primera y la última ejecución el error ha pasado de ' + num(first.rmse) + ' a ' + num(lastRun.rmse) + ' MW (' + sgn((first.rmse - lastRun.rmse) / first.rmse * 100, 1) + ' %). La línea gris es la mejor marca hasta cada momento; el objetivo es cruzar la línea discontinua del operador.'
      : 'Con una sola ejecución todavía no hay tendencia; cada nueva prueba añadirá un punto.';
  })();

  /* ============ Pie ============ */
  (function () {
    var gen = new Date(D.meta.generated_at), V = D.meta.versions || {};
    var fm = new Intl.DateTimeFormat('es-ES', { dateStyle: 'long', timeStyle: 'short', timeZone: 'Europe/Madrid' }).format(gen);
    $('#footDate').textContent = fm.replace(',', ' a las') + ' (hora de Madrid)';
    var utc = D.meta.generated_at.replace('T', ' ').slice(0, 16) + ' UTC';
    var rows = [
      ['Generado', utc],
      ['Plan del proyecto', P ? planSrc() : '—'],
      ['Instantánea de la previsión guardada', V.bronze_snapshot || 'no disponible'],
      ['Conjunto de dynamical.org', V.dataset_version ? 'versión ' + V.dataset_version : '—'],
      ['Previsión guardada', V.ifs_range && V.ifs_range[0] ? dateLong(V.ifs_range[0]) + ' a ' + dateLong(V.ifs_range[1]) : '—'],
      ['Registro de aerogeneradores', V.mastr_export ? 'exportación del ' + dateLong(V.mastr_export) : '—'],
      ['Última descarga de ENTSO-E', V.entsoe_last_fetch ? V.entsoe_last_fetch.replace('T', ' ').slice(0, 16) + ' UTC' : '—']
    ];
    $('#footVersions').innerHTML = rows.map(function (r) { return '<div><dt>' + esc(r[0]) + '</dt><dd>' + esc(r[1]) + '</dd></div>'; }).join('');
  })();

  /* ---------- inicio de glosario y redimensionado ---------- */
  $$('.gl').forEach(function (g) { g.setAttribute('tabindex', '0'); });
  var rt = null;
  window.addEventListener('resize', function () {
    clearTimeout(rt);
    rt = setTimeout(function () { fitHeight('map', ZONE_VIEW); fitHeight('world', WORLD_VIEW); fitHeight('worldEu', EU_VIEW); fitHeight('fGrid', GRID_VIEW); Object.keys(reg).forEach(function (id) { if (reg[id].inst) reg[id].inst.resize(); }); renderTimeline(); }, 140);
  });
  window.__reportReady = true;
  if (Q.has('probe')) { setTimeout(function () { var w = [], vw = document.documentElement.clientWidth; $$('body *').forEach(function (e) { var r = e.getBoundingClientRect(); if (r.right > vw + 1 && !e.closest('.nav-links') && !e.closest('canvas')) w.push(e.tagName + '.' + e.className + ':' + Math.round(r.right)); }); document.body.setAttribute('data-probe', document.documentElement.scrollWidth + '/' + vw + ' ' + w.slice(0, 8).join(' | ')); }, 500); }
})();
