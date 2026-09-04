/* app.js - the page. Every fact comes from Python, over one POST per call.

   Nothing here holds state that Python already holds. The page asks, draws
   what it gets, and asks again.

   api().whatever(a, b) used to reach Python through a webview bridge. It
   now posts to /api/whatever with [a, b] as the JSON body and unwraps the
   {result} or {error} that comes back, but every call site above this line
   still reads the same way, because the shape of the call did not change. */

const $ = (id) => document.getElementById(id);

async function call(name, args) {
    const res = await fetch(`/api/${name}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(args),
    });
    const body = await res.json();
    if (body.error) throw new Error(body.error);
    return body.result;
}

const api = () => new Proxy({}, {
    get: (_, name) => (...args) => call(name, args),
});

/* A page that throws goes quiet and looks broken. Keep what it threw, and put
   the first line where it can be read. */
window.__errors = [];
window.addEventListener("error", (event) => {
    window.__errors.push(`${event.message} (${event.filename}:${event.lineno})`);
    const bar = document.getElementById("status");
    if (bar) bar.textContent = window.__errors[0];
});
window.addEventListener("unhandledrejection", (event) => {
    window.__errors.push(String(event.reason));
    const bar = document.getElementById("status");
    if (bar) bar.textContent = window.__errors[0];
});

const state = {
    view: "search",
    pick: null,          // the chosen section id
    charm: null,         // the chosen charm row
    charms: [],
    charmSort: null,     // {key, dir}, or null for the server's own order
    characters: [],
    character: null,     // the full record of the open character
    pickingCharmsFor: null, // {id, name}, while the Charm Library is adding to a sheet
    source: "",
    page: 0,
};

function say(message) { $("status").textContent = message || ""; }

function escape(text) {
    return String(text ?? "").replace(/[&<>]/g,
        (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

/* ------------------------------------------------------------- the views */

function showView(name) {
    state.view = name;
    document.querySelectorAll(".tab").forEach((tab) =>
        tab.classList.toggle("on", tab.dataset.view === name));
    // A view can belong to more than one tab: the excerpt is read beside the
    // results and beside the answer.
    document.querySelectorAll(".view").forEach((view) =>
        view.classList.toggle("hidden",
            !view.dataset.view.split(" ").includes(name)));
    if (name === "library") { loadFilters(); updatePickingBanner(); }
    if (name === "characters") loadCharacters();
    if (name === "ask") $("question").focus();
    // The editor and the importer are tools, not reading. They take the width.
    const wide = name === "bookmarks" || name === "import";
    $("sash").classList.toggle("hidden", wide);
    $("right").classList.toggle("hidden", wide);
    $("left").style.flexBasis = wide ? "100%" : "";
    enterCharacterLayout(name === "characters");
}

/* A character sheet wants the opposite balance of everything else: the
   shelf of books is not what you are looking at, and the roster list
   matters far less than the sheet itself. Entering restores on the way out,
   but only if this is the state the view actually changed - a plain switch
   between, say, Search and Ask must never touch either. */
let shelfClosedBeforeCharacters = null;
let splitBeforeCharacters = null;

function enterCharacterLayout(entering) {
    if (entering) {
        if (shelfClosedBeforeCharacters === null) {
            shelfClosedBeforeCharacters = $("shelf").classList.contains("closed");
            splitBeforeCharacters = document.documentElement.style.getPropertyValue("--split");
        }
        setShelfClosed(true);
        document.documentElement.style.setProperty("--split", "28%");
    } else if (shelfClosedBeforeCharacters !== null) {
        setShelfClosed(shelfClosedBeforeCharacters);
        if (splitBeforeCharacters) {
            document.documentElement.style.setProperty("--split", splitBeforeCharacters);
        } else {
            document.documentElement.style.removeProperty("--split");
        }
        shelfClosedBeforeCharacters = null;
        splitBeforeCharacters = null;
    }
}

function setShelfClosed(closed) {
    const shelf = $("shelf");
    shelf.classList.toggle("closed", closed);
    $("shelf-toggle").textContent = closed ? "›" : "‹";
}

/* ------------------------------------------------------------- the shelf */

function drawShelf(books, collection) {
    $("collection").textContent = collection;
    $("shelf-body").innerHTML = books.map((book) => `
        <div class="book" data-id="${book.id}" data-title="${escape(book.title)}"
             data-found="${book.found ? 1 : 0}"
             title="${escape(book.source || "")}">
            <h4>${escape(book.title)}</h4>
            <div class="meta">${book.pages} pages · ${book.chunks} sections</div>
            ${book.found ? ""
                : '<div class="meta missing">PDF not found · right click to find it</div>'}
        </div>`).join("");
    // A right click acts on the card under the pointer, and never opens it.
    $("shelf-body").querySelectorAll(".book").forEach((card) => {
        card.oncontextmenu = (event) => showBookMenu(event, card);
        addCover(card);
    });
    drawCollections();
}

/* Covers come one at a time, after the card is drawn. A shelf of them is a
   megabyte of PNG, and the names should not wait on the pictures. */
async function addCover(card) {
    const uri = await api().cover(Number(card.dataset.id));
    if (!uri) return;
    const image = document.createElement("img");
    image.className = "cover";
    image.src = uri;
    image.alt = "";
    card.prepend(image);
}

async function drawCollections() {
    const shelf = await api().shelf();
    if (shelf.length < 2) { $("collections").innerHTML = ""; return; }
    $("collections").innerHTML = "<div class='shelf-head'>Collections</div>"
        + shelf.map((one) => `
            <div class="book ${one.open ? "open" : ""}" data-path="${escape(one.path)}">
                <h4>${escape(one.name)}</h4>
            </div>`).join("");
    $("collections").querySelectorAll(".book").forEach((card) => {
        card.onclick = () => openCollection(card.dataset.path);
        card.oncontextmenu = (event) => {
            event.preventDefault();
            const menu = $("collection-menu");
            menu.dataset.path = card.dataset.path;
            menu.dataset.open = card.classList.contains("open") ? "1" : "0";
            menu.style.left = `${event.clientX}px`;
            menu.style.top = `${event.clientY}px`;
            menu.classList.remove("hidden");
        };
    });
}

async function openCollection(path) {
    say("Opening…");
    const out = await api().open_index(path);
    if (!out.ok) { say(out.message); return; }
    filtersLoaded = false;
    $("results").innerHTML = "";
    $("excerpt").innerHTML = "";
    $("transcript").innerHTML = "";
    drawShelf(out.books, out.collection);
    say(`${out.collection} · ${out.books.length} books`);
}

/* ------------------------------------------------------------ the search */

async function runSearch(event) {
    if (event) event.preventDefault();
    const terms = $("terms").value.trim();
    if (!terms) return;
    say("Searching…");
    const found = await api().search(terms);
    drawResults(found);
    say(found.message);
}

function drawResults(found) {
    const body = $("results");
    body.innerHTML = found.results.map((row) => `
        <tr data-id="${row.id}">
            <td>${escape(row.book)}</td>
            <td>${escape(row.section)}${row.cited
                ? ' <span class="tag">(cross-reference)</span>' : ""}</td>
            <td class="num">${escape(row.pages)}</td>
        </tr>`).join("");
    body.querySelectorAll("tr").forEach((tr) => {
        tr.onclick = () => pickResult(Number(tr.dataset.id));
        tr.ondblclick = () => openPdf();
    });
    if (found.best) pickResult(found.best, true);
    else $("excerpt").innerHTML = "";
}

async function pickResult(id, scroll) {
    state.pick = id;
    document.querySelectorAll("#results tr, #ask-sources tr").forEach((tr) =>
        tr.classList.toggle("on", Number(tr.dataset.id) === id));
    const one = await api().excerpt(id);
    state.source = one.source;
    state.page = one.page;
    const head = [one.book, one.pages, one.path].filter(Boolean).join("  ·  ");
    $("excerpt").innerHTML = `<div class="head">${escape(head)}</div>`
        + mark(one.html, one.terms);
    $("excerpt").scrollTop = 0;
    if (scroll) {
        const row = document.querySelector(`#results tr[data-id="${id}"]`);
        if (row) row.scrollIntoView({ block: "nearest" });
    }
}

/* The search words are marked in the page, not in Python, so the offsets of
   the style runs are never disturbed. */
function mark(html, terms) {
    if (!terms || !terms.length) return html;
    const safe = terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    const pattern = new RegExp(`(${safe.join("|")})`, "gi");
    return html.replace(/>([^<]+)</g, (whole, text) =>
        ">" + text.replace(pattern, "<mark>$1</mark>") + "<");
}

async function openPdf() {
    if (!state.source) { say("No PDF recorded for that book."); return; }
    const out = await api().open_pdf(state.source, state.page);
    say(out.message);
}

/* --------------------------------------------------------------- the ask */

/* The page pulls events; Python never calls into the window.

   A call from a Python worker thread into the webview blocks on a synchronous
   cross-thread Invoke and holds the GIL across it. While the window is being
   dragged, Windows runs a modal loop, the UI thread cannot answer, and the
   whole process locks up. Pulling has no such trap. */
async function pump() {
    try {
        const events = await api().poll();
        events.forEach((event) => window.onEvent(event));
    } catch (err) {
        /* the window is closing */
    }
}

window.onEvent = function (event) {
    if (event.kind === "searching") {
        addTurn("Question", escape(event.question));
        say("Searching the books…");
    } else if (event.kind === "sources") {
        drawSources(event.results);
        say(`${event.results.length} sections found.`);
    } else if (event.kind === "thinking") {
        say("Waiting for the model…");
    } else if (event.kind === "answer") {
        addTurn("Answer", shapeAnswer(event.text));
        say(`Answered from ${$("ask-sources").children.length} sections. `
            + "Click a citation to read it.");
        asking(false);
    } else if (event.kind === "failed") {
        addTurn("Answer", `<p class="warn">${escape(event.message)}</p>`);
        say(event.message);
        asking(false);
    } else if (event.kind === "checked") {
        say(`Checked ${event.done} of ${event.total}: ${event.name}`);
    } else if (event.kind === "indexing") {
        say(`${event.stage}${event.total ? ` ${event.done}/${event.total}` : ""}`);
    } else if (event.kind === "indexed") {
        $("im-index").disabled = false;
        drawShelf(event.books, event.collection);
        filtersLoaded = false;
        say(`${event.name} is ready. The window is now searching it.`);
    } else if (event.kind === "index_failed") {
        $("im-index").disabled = false;
        say(event.message);
    }
};

function asking(busy) {
    ["ask-new", "ask-more"].forEach((id) => { $(id).disabled = busy; });
}

function addTurn(label, html) {
    const turn = document.createElement("div");
    turn.className = "turn";
    turn.innerHTML = `<div class="turn-label">${label}</div>${html}`;
    $("transcript").appendChild(turn);
    $("transcript").scrollTop = $("transcript").scrollHeight;
}

/* The model writes light markdown and cites with [#id p.N]. */
function shapeAnswer(text) {
    return text.split(/\n{2,}/).filter((b) => b.trim()).map((block) => {
        const lines = block.split("\n").filter((l) => l.trim());
        const bullets = lines.every((l) => /^\s*[-*•]\s+/.test(l));
        if (bullets) {
            return "<ul>" + lines.map((l) =>
                `<li>${inline(l.replace(/^\s*[-*•]\s+/, ""))}</li>`).join("") + "</ul>";
        }
        return lines.map((l) => `<p>${inline(l)}</p>`).join("");
    }).join("");
}

function inline(line) {
    let html = escape(line);
    html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/(^|[\s(])\*([^*]+)\*/g, "$1<em>$2</em>");
    html = html.replace(/(^|[\s(])_([^_]+)_/g, "$1<em>$2</em>");
    // A citation is a button: it opens the passage it points at.
    return html.replace(/\[#(\d+)[^\]]*?(\d+)\]/g,
        (whole, id, page) =>
            `<a class="cite" data-id="${id}" href="#">#${id} p.${page}</a>`);
}

async function askQuestion(follow) {
    const question = $("question").value.trim();
    if (!question) return;
    $("question").value = "";
    asking(true);
    const out = await api().ask(question, follow);
    if (!out.started) {
        asking(false);
        say(out.message || "Nothing to ask.");
    }
}

function drawSources(rows) {
    const body = $("ask-sources");
    body.innerHTML = rows.map((row) => `
        <tr data-id="${row.id}">
            <td>${escape(row.book)}</td>
            <td>${escape(row.section)}</td>
            <td class="num">${escape(row.pages)}</td>
        </tr>`).join("");
    body.querySelectorAll("tr").forEach((tr) => {
        tr.onclick = () => pickResult(Number(tr.dataset.id));
        tr.ondblclick = () => openPdf();
    });
}

/* A citation in the answer opens the passage, wherever the click lands. */
document.addEventListener("click", (event) => {
    const cite = event.target.closest(".cite");
    if (!cite) return;
    event.preventDefault();
    pickResult(Number(cite.dataset.id));
});

/* ------------------------------------------------------------ the library */

let filtersLoaded = false;

async function loadFilters() {
    if (filtersLoaded) return;
    const filters = await api().charm_filters();
    if (!filters.built) {
        say("No Charm library yet. Press Build the library.");
        return;
    }
    fill("f-book", filters.books, prettyBook);
    fill("f-tree", filters.trees);
    fill("f-type", filters.types);
    fill("f-keyword", filters.keywords);
    fill("f-essence", filters.essence.map(String));
    filtersLoaded = true;
    runCharmSearch();
}

function fill(id, values, labelOf) {
    $(id).innerHTML = '<option value="">Any</option>'
        + values.map((v) => `<option value="${escape(v)}">`
            + `${escape(labelOf ? labelOf(v) : v)}</option>`).join("");
    $(id).onchange = runCharmSearch;
}

/* Book titles come straight off the source PDFs' file names: hyphens for
   spaces, a stray "(small)" from compression, and an "Ex3"/"Exalted-3e"
   prefix that just names the game line every book here already belongs to.
   The raw title is still what search filters on; this is display only. */
function prettyBook(title) {
    return (title || "")
        .replace(/\s*\(small\)\s*$/i, "")
        .replace(/^(EX-?3|Exalted-3e)-/i, "")
        .replace(/-/g, " ")
        .trim();
}

async function runCharmSearch(event) {
    if (event) event.preventDefault();
    const rows = await api().charm_search({
        terms: $("charm-terms").value,
        book: $("f-book").value,
        tree: $("f-tree").value,
        type: $("f-type").value,
        keyword: $("f-keyword").value,
        essence: $("f-essence").value,
    });
    state.charms = rows;
    state.charmSort = null;       // a fresh search reverts to the server's own order
    renderCharmRows();
    say(`${rows.length} charms.`);
    if (rows.length) pickCharm(0);
    else $("charm-detail").innerHTML = "";
}

function renderCharmRows() {
    const rows = state.charms;
    $("charm-rows").innerHTML = rows.map((row, i) => `
        <tr data-i="${i}">
            <td>${escape(row.name)}</td>
            <td>${escape(row.tree)}</td>
            <td class="tag">${escape(row.cost)}</td>
            <td class="num">${row.essence || ""}</td>
            <td>${escape(prettyBook(row.book))}</td>
            <td class="num">${row.page}</td>
        </tr>`).join("");
    $("charm-rows").querySelectorAll("tr").forEach((tr) => {
        tr.onclick = () => pickCharm(Number(tr.dataset.i));
    });
    document.querySelectorAll("#library-table th.sortable .arrow").forEach((arrow) => {
        const th = arrow.closest("th");
        arrow.textContent = state.charmSort && th.dataset.sort === state.charmSort.key
            ? (state.charmSort.dir < 0 ? " ▲" : " ▼") : "";
    });
}

/* Charm/Tree/Book headers sort the current results in place, alphabetically.
   A second click on the same header reverses direction; the server's own
   order (tree, essence, rating, name) comes back on the next search. */
function sortCharms(key) {
    const same = state.charmSort && state.charmSort.key === key;
    const dir = same ? -state.charmSort.dir : 1;
    state.charmSort = { key, dir };
    const value = (row) => key === "book" ? prettyBook(row.book) : (row[key] || "");
    state.charms = [...state.charms].sort((a, b) =>
        value(a).localeCompare(value(b), undefined, { sensitivity: "base" }) * dir);
    renderCharmRows();
}

/* The reader's Original/Simplified choice is a per-viewer convenience, not
   app state, so it lives in localStorage rather than being asked of the
   server. A browser that blocks storage (a private window, say) just falls
   back to the default every time - not worth failing the page over. */
function charmTextMode() {
    try {
        return localStorage.getItem("charmTextMode") || "simple";
    } catch {
        return "simple";
    }
}

function setCharmTextMode(mode) {
    try {
        localStorage.setItem("charmTextMode", mode);
    } catch {
        /* no storage available; the choice just does not stick */
    }
}

function pickCharm(i) {
    const row = state.charms[i];
    if (!row) return;
    state.charm = row;
    document.querySelectorAll("#charm-rows tr").forEach((tr) =>
        tr.classList.toggle("on", Number(tr.dataset.i) === i));
    const field = (label, value) =>
        `<dt>${label}</dt><dd>${escape(value || "None")}</dd>`;
    const mode = charmTextMode();
    const hasSimple = !!(row.simple_text && row.simple_text.trim());
    const showingSimple = mode === "simple" && hasSimple;
    const body = showingSimple ? row.simple_text : row.text;
    const missing = mode === "simple" && !hasSimple
        ? ' <span class="hint">(not simplified yet — showing the original)</span>' : "";
    const modeTab = (value, label) => `<button type="button"
        class="tab${mode === value ? " on" : ""}" data-mode="${value}">${label}</button>`;
    $("charm-detail").innerHTML = `
        <h2>${escape(row.name)}</h2>
        <dl>
            ${field("Cost", row.cost)}
            ${field("Mins", row.mins)}
            ${field("Type", row.type)}
            ${field("Keywords", row.keywords)}
            ${field("Duration", row.duration)}
            ${field("Prerequisite Charms", row.prereqs)}
        </dl>
        <div class="tabs" style="margin-top:14px">
            ${modeTab("simple", "Simplified")}${modeTab("original", "Original")}${missing}
        </div>
        <p style="font-family:var(--read);font-size:1.14rem;margin-top:10px">
            ${escape(body)}</p>
        <div class="head" style="font:12px var(--mono);color:var(--muted);
             margin-top:18px">${escape(prettyBook(row.book))} · page ${row.page}</div>`;
    $("charm-detail").querySelectorAll("[data-mode]").forEach((btn) => {
        btn.onclick = () => { setCharmTextMode(btn.dataset.mode); pickCharm(i); };
    });
}

async function buildCharms() {
    $("charm-build").disabled = true;
    say("Reading the books…");
    const out = await api().build_charms();
    $("charm-build").disabled = false;
    filtersLoaded = false;
    say(`The library holds ${out.count} charms.`);
    loadFilters();
}

/* -------------------------------------------------------------- characters */

const ATTRIBUTES = ["strength", "dexterity", "stamina", "charisma",
    "manipulation", "appearance", "perception", "intelligence", "wits"];

let charactersLoaded = false;

async function loadCharacters(force) {
    if (charactersLoaded && !force) return;
    state.characters = await api().character_list();
    renderCharacterRows();
    charactersLoaded = true;
    if (state.characters.length && !state.character) {
        pickCharacter(state.characters[0].id);
    }
}

function renderCharacterRows() {
    $("char-rows").innerHTML = state.characters.map((row) => `
        <tr data-id="${row.id}" class="${state.character && state.character.id === row.id ? "on" : ""}">
            <td>${escape(row.name)}</td>
            <td>${escape(row.caste)}</td>
            <td>${escape(row.concept)}</td>
        </tr>`).join("");
    $("char-rows").querySelectorAll("tr").forEach((tr) => {
        tr.onclick = () => pickCharacter(Number(tr.dataset.id));
    });
}

async function pickCharacter(id) {
    state.character = await api().character_get(id);
    renderCharacterRows();
    renderCharacterSheet();
}

function renderCharacterSheet() {
    const c = state.character;
    if (!c) { $("character-detail").innerHTML = ""; return; }

    // An add or a remove rebuilds the whole sheet - fresh rows need fresh
    // ids - which would otherwise slam every <details> back to its default
    // state. Empty means this is the first render (a fresh character, or
    // the tab just opened), so the template's own defaults stand; anything
    // else means a re-render of the same sheet, and every section keeps
    // exactly the open/closed state it already had.
    const existing = $("character-detail").querySelectorAll("details[data-section]");
    const openSections = new Set(Array.from(existing)
        .filter((d) => d.open).map((d) => d.dataset.section));
    const isReRender = existing.length > 0;

    const field = (name, label, wide) => `
        <label class="hint" style="display:flex;flex-direction:column;gap:3px;
             ${wide ? "flex:1 1 220px" : "flex:0 0 140px"}">${label}
            <input type="text" value="${escape(c[name] || "")}" data-field="${name}"
                   style="padding:6px 8px;border:1px solid var(--rule);
                          border-radius:var(--radius)"></label>`;

    const attr = (name, label, max) => `
        <label class="hint" style="display:flex;flex-direction:column;gap:3px;width:8em">
            ${label}
            <input type="number" min="0" max="${max || 5}" value="${c[name] || 0}" data-attr="${name}"
                   style="padding:6px 8px;border:1px solid var(--rule);
                          border-radius:var(--radius)"></label>`;

    // A cell editable in place, shared by Merits/Weapons/Armor/Intimacies/
    // Inventory - every one of those is just rows a character owns freely,
    // saved and removed the same way regardless of which fields it has.
    const rowCell = (table, id, key, value, width, type) => `
        <td><input type="${type || "text"}" value="${escape(value ?? "")}"
            data-row-table="${table}" data-row-id="${id}" data-row-field="${key}"
            ${width ? `style="width:${width}"` : ""}></td>`;
    const removeCell = (table, id, label) => `
        <td><button type="button" class="icon-btn" data-row-remove="${table}:${id}"
                title="Remove ${escape(label)}"><i class="fa-solid fa-trash"></i></button></td>`;

    const abilityRow = (a) => `
        <tr data-id="${a.id}">
            <td><label class="hint" style="padding: 4px 0;display:flex;gap:6px;align-items:center">
                <input type="checkbox" data-ability-favored="${a.id}" ${a.favored ? "checked" : ""}>
                ${escape(a.name)}</label></td>
            <td class="num"><input type="number" min="0" max="5" value="${a.rating}"
                data-ability-rating="${a.id}" style="width:3.5em"></td>
            <td><button type="button" data-ability-remove="${a.id}" class="icon-btn" hidden
                    title="Remove ${escape(a.name)}"><i class="fa-solid fa-trash"></i></button></td>
        </tr>`;

    const knownCharms = c.charms || [];
    const charmRow = (kc) => kc.manual ? `
        <tr data-id="${kc.id}" data-charm-id="" draggable="true">
            <td class="drag-handle" title="Drag to reorder"><i class="fa-solid fa-grip-vertical"></i></td>
            ${rowCell("charms_known_manual", kc.id, "name", kc.name)}
            <td><input type="text" value="${escape(kc.type)}" style="width:5.5em"
                data-row-table="charms_known_manual" data-row-id="${kc.id}" data-row-field="type"></td>
            <td class="tag"><input type="text" value="${escape(kc.cost)}" style="width:4.5em"
                data-row-table="charms_known_manual" data-row-id="${kc.id}" data-row-field="cost"></td>
            ${rowCell("charms_known_manual", kc.id, "book", kc.book)}
            <td class="num"><input type="number" value="${kc.page ?? ""}" style="width:3.5em"
                data-row-table="charms_known_manual" data-row-id="${kc.id}" data-row-field="page"></td>
        </tr>` : `
        <tr data-id="${kc.id}" data-charm-id="${kc.charm_id}" draggable="true">
            <td class="drag-handle" title="Drag to reorder"><i class="fa-solid fa-grip-vertical"></i></td>
            <td>${escape(kc.name)}</td>
            <td>${escape(kc.type)}</td>
            <td class="tag">${escape(kc.cost)}</td>
            <td>${escape(prettyBook(kc.book))}</td>
            <td class="num">${kc.page || ""}</td>
        </tr>`;

    const meritRow = (m) => `<tr>
        ${rowCell("merits", m.id, "name", m.name)}
        ${rowCell("merits", m.id, "rating", m.rating, "3.5em", "number")}
        ${removeCell("merits", m.id, m.name || "this Merit")}</tr>`;

    const weaponRow = (w) => `<tr>
        ${rowCell("weapons", w.id, "name", w.name)}
        ${rowCell("weapons", w.id, "acc", w.acc, "3.5em")}
        ${rowCell("weapons", w.id, "dmg", w.dmg, "3.5em")}
        ${rowCell("weapons", w.id, "def", w.def, "3.5em")}
        ${rowCell("weapons", w.id, "ovw", w.ovw, "3.5em")}
        ${rowCell("weapons", w.id, "tags", w.tags)}
        ${rowCell("weapons", w.id, "dice_pool", w.dice_pool, "4.5em")}
        ${removeCell("weapons", w.id, w.name || "this weapon")}</tr>`;

    const armorRow = (a) => `<tr>
        ${rowCell("armor", a.id, "name", a.name)}
        ${rowCell("armor", a.id, "soak", a.soak, "3.5em")}
        ${rowCell("armor", a.id, "hardness", a.hardness, "3.5em")}
        ${rowCell("armor", a.id, "mobility", a.mobility, "3.5em")}
        ${rowCell("armor", a.id, "tags", a.tags)}
        ${removeCell("armor", a.id, a.name || "this armor")}</tr>`;

    const intimacyRow = (i) => `<tr>
        ${rowCell("intimacies", i.id, "description", i.description)}
        ${rowCell("intimacies", i.id, "intensity", i.intensity, "7em")}
        ${removeCell("intimacies", i.id, i.description || "this Intimacy")}</tr>`;

    const inventoryRow = (item) => `<tr>
        ${rowCell("inventory", item.id, "text", item.text)}
        ${removeCell("inventory", item.id, item.text || "this item")}</tr>`;

    const experiencePurchaseRow = (p) => `<tr>
        ${rowCell("experience_purchases", p.id, "date", p.date, "9.5em", "date")}
        ${rowCell("experience_purchases", p.id, "cost", p.cost, "5.5em", "number")}
        ${rowCell("experience_purchases", p.id, "bought", p.bought)}
        ${removeCell("experience_purchases", p.id, p.bought || "this purchase")}</tr>`;

    $("character-detail").innerHTML = `
        <div class="field" style="flex-wrap:wrap;padding:0 0 14px">
            ${field("name", "Name", true)}${field("player", "Player")}
            ${field("caste", "Caste")}${field("concept", "Concept", true)}
            ${field("anima", "Anima", true)}${field("supernal_ability", "Supernal Ability")}
        </div>
        <h2 style="font-size:15px;margin:0 0 8px">Attributes</h2>
        <div style="display:grid;grid-template-columns:repeat(3, 8em);
             grid-template-rows:repeat(3, auto);grid-auto-flow:column;
             gap:10px;padding:0 0 16px">
            ${ATTRIBUTES.map((a) => attr(a, a[0].toUpperCase() + a.slice(1))).join("")}
        </div>
        <details open data-section="abilities" style="margin-bottom:12px">
            <summary style="cursor:pointer;font-size:15px;font-weight:600">Abilities</summary>
            <div class="field" style="padding:8px 0 0">
                <input type="text" id="ability-new-name" placeholder="Custom Ability…" style="flex:1">
                <button type="button" id="ability-new-add" class="icon-btn add-btn"
                        title="Add a custom Ability"><i class="fa-solid fa-plus"></i></button>
            </div>
            <label class="hint" style="display:flex;gap:6px;align-items:center;padding:8px 0 4px">
                <input type="checkbox" id="ability-remove-mode"> Allow removing abilities</label>
            <table class="rows" style="margin-bottom:12px">
                <thead><tr><th>Ability</th><th style="width:70px">Rating</th><th style="width:80px"></th></tr></thead>
                <tbody>${c.abilities.map(abilityRow).join("")}</tbody>
            </table>
        </details>
        <details open data-section="charms" style="margin-bottom:12px">
            <summary style="cursor:pointer;font-size:15px;font-weight:600">Charms Known</summary>
            <div class="field" style="padding:8px 0 0">
                    <button type="button" data-add="charms_known_manual" class="icon-btn add-btn"
                        title="Add a Charm by hand"><i class="fa-solid fa-plus"></i></button>
                        <div style="display: flex;align-self: anchor-center">OR</div>
                    <button type="button" id="charm-add-open" class="icon-btn add-btn">Find a Charm to add…</button>
                <span class="hint">Drag the handle to reorder. Right-click a Charm to view or delete it.</span>
            </div>
            <table class="rows" id="known-charms-table" style="margin-top:8px">
                <thead><tr>
                    <th style="width:28px"></th>
                    <th>Charm</th>
                    <th style="width:14%">Type</th>
                    <th style="width:86px">Cost</th>
                    <th style="width:20%">Book</th>
                    <th style="width:56px">Page</th>
                </tr></thead>
                <tbody id="known-charm-rows" data-rows="charms_known_manual">${knownCharms.length ? knownCharms.map(charmRow).join("")
        : '<tr><td colspan="6" class="hint">No Charms known yet.</td></tr>'}</tbody>
            </table>
        </details>
        <details data-section="tracks" style="margin-bottom:12px">
            <summary style="cursor:pointer;font-size:15px;font-weight:600">Willpower, Essence &amp; Limit</summary>
            <div class="field" style="flex-wrap:wrap;padding:8px 0 0">
                ${attr("willpower_rating", "Willpower", 10)}${attr("willpower_current", "WP Current", 10)}
                ${attr("essence_rating", "Essence", 10)}
                ${attr("personal_motes", "Personal", 99)}${attr("personal_committed", "Pers. Committed", 99)}
                ${attr("peripheral_motes", "Peripheral", 99)}${attr("peripheral_committed", "Periph. Committed", 99)}
            </div>
            <div class="field" style="flex-wrap:wrap;padding:8px 0 16px">
                ${field("limit_trigger", "Limit Trigger", true)}${attr("limit_current", "Limit", 10)}
            </div>
        </details>
        <details data-section="health" style="margin-bottom:12px">
            <summary style="cursor:pointer;font-size:15px;font-weight:600">Health</summary>
            <div class="field" style="flex-wrap:wrap;padding:8px 0 16px">
                ${attr("health_boxes", "Health Boxes", 30)}${attr("bashing", "Bashing", 30)}
                ${attr("lethal", "Lethal", 30)}${attr("aggravated", "Aggravated", 30)}
            </div>
        </details>
        <details data-section="merits" style="margin-bottom:12px">
            <summary style="cursor:pointer;font-size:15px;font-weight:600">Merits</summary>
            <div class="field" style="padding:8px 0 0">
                <button type="button" data-add="merits" class="icon-btn add-btn"
                        title="Add a Merit"><i class="fa-solid fa-plus"></i></button>
            </div>
            <table class="rows" style="margin-top:8px">
                <thead><tr><th>Merit</th><th style="width:70px">Rating</th><th style="width:44px"></th></tr></thead>
                <tbody data-rows="merits">${c.merits.length ? c.merits.map(meritRow).join("")
        : '<tr><td colspan="3" class="hint">None yet.</td></tr>'}</tbody>
            </table>
        </details>
        <details data-section="weapons" style="margin-bottom:12px">
            <summary style="cursor:pointer;font-size:15px;font-weight:600">Weapons</summary>
            <div class="field" style="padding:8px 0 0">
                <button type="button" data-add="weapons" class="icon-btn add-btn"
                        title="Add a Weapon"><i class="fa-solid fa-plus"></i></button>
            </div>
            <table class="rows" style="margin-top:8px">
                <thead><tr>
                    <th>Weapon</th><th style="width:56px">Acc</th><th style="width:56px">Dmg</th>
                    <th style="width:56px">Def</th><th style="width:56px">Ovw</th>
                    <th>Tags</th><th style="width:72px">Pool</th><th style="width:44px"></th>
                </tr></thead>
                <tbody data-rows="weapons">${c.weapons.length ? c.weapons.map(weaponRow).join("")
        : '<tr><td colspan="8" class="hint">None yet.</td></tr>'}</tbody>
            </table>
        </details>
        <details data-section="armor" style="margin-bottom:12px">
            <summary style="cursor:pointer;font-size:15px;font-weight:600">Armor</summary>
            <div class="field" style="padding:8px 0 0">
                <button type="button" data-add="armor" class="icon-btn add-btn"
                        title="Add Armor"><i class="fa-solid fa-plus"></i></button>
            </div>
            <table class="rows" style="margin-top:8px">
                <thead><tr>
                    <th>Armor</th><th style="width:56px">Soak</th><th style="width:56px">Hard</th>
                    <th style="width:56px">Mob.</th><th>Tags</th><th style="width:44px"></th>
                </tr></thead>
                <tbody data-rows="armor">${c.armor.length ? c.armor.map(armorRow).join("")
        : '<tr><td colspan="6" class="hint">None yet.</td></tr>'}</tbody>
            </table>
        </details>
        <details data-section="intimacies" style="margin-bottom:12px">
            <summary style="cursor:pointer;font-size:15px;font-weight:600">Intimacies</summary>
            <div class="field" style="padding:8px 0 0">
                <button type="button" data-add="intimacies" class="icon-btn add-btn"
                        title="Add an Intimacy"><i class="fa-solid fa-plus"></i></button>
            </div>
            <table class="rows" style="margin-top:8px">
                <thead><tr><th>Intimacy</th><th style="width:9em">Intensity</th><th style="width:44px"></th></tr></thead>
                <tbody data-rows="intimacies">${c.intimacies.length ? c.intimacies.map(intimacyRow).join("")
        : '<tr><td colspan="3" class="hint">None yet.</td></tr>'}</tbody>
            </table>
        </details>
        <details data-section="inventory" style="margin-bottom:12px">
            <summary style="cursor:pointer;font-size:15px;font-weight:600">Inventory</summary>
            <div class="field" style="padding:8px 0 0">
                <button type="button" data-add="inventory" class="icon-btn add-btn"
                        title="Add an Item"><i class="fa-solid fa-plus"></i></button>
            </div>
            <table class="rows" style="margin-top:8px">
                <thead><tr><th>Item</th><th style="width:44px"></th></tr></thead>
                <tbody data-rows="inventory">${c.inventory.length ? c.inventory.map(inventoryRow).join("")
        : '<tr><td colspan="2" class="hint">None yet.</td></tr>'}</tbody>
            </table>
        </details>
        <details data-section="experience" style="margin-bottom:12px">
            <summary style="cursor:pointer;font-size:15px;font-weight:600">Experience</summary>
            <div class="field" style="flex-wrap:wrap;padding:8px 0 0">
                ${attr("exp_current", "XP Current", 999)}${attr("exp_total", "XP Total", 999)}
                ${attr("solar_exp_current", "Solar XP Current", 999)}${attr("solar_exp_total", "Solar XP Total", 999)}
            </div>
            <div class="field" style="padding:8px 0 0">
                <button type="button" data-add="experience_purchases" class="icon-btn add-btn"
                        title="Add a purchase"><i class="fa-solid fa-plus"></i></button>
            </div>
            <table class="rows" style="margin-top:8px">
                <thead><tr><th style="width:8em">Date</th><th style="width:5.5em">Cost</th>
                    <th>Bought</th><th style="width:44px"></th></tr></thead>
                <tbody data-rows="experience_purchases">${c.experience_purchases.length
                    ? c.experience_purchases.map(experiencePurchaseRow).join("")
                    : '<tr><td colspan="4" class="hint">None yet.</td></tr>'}</tbody>
            </table>
        </details>
        <details data-section="notes" style="margin-bottom:12px">
            <summary style="cursor:pointer;font-size:15px;font-weight:600">Notes</summary>
            <textarea data-field="notes" style="width:100%;min-height:120px;padding:8px;
                margin-top:8px;border:1px solid var(--rule);border-radius:var(--radius);
                font:inherit;resize:vertical">${escape(c.notes || "")}</textarea>
        </details>`;

    if (isReRender) {
        $("character-detail").querySelectorAll("details[data-section]").forEach((d) => {
            d.open = openSections.has(d.dataset.section);
        });
    }

    $("character-detail").querySelectorAll("[data-field]").forEach((input) => {
        input.onchange = () => saveCharacterField(input.dataset.field, input.value);
    });
    $("character-detail").querySelectorAll("[data-attr]").forEach((input) => {
        input.onchange = () => saveCharacterField(input.dataset.attr, Number(input.value));
    });
    $("character-detail").querySelectorAll("[data-ability-rating]").forEach((input) => {
        input.onchange = () => api().ability_save(
            Number(input.dataset.abilityRating), Number(input.value), null);
    });
    $("character-detail").querySelectorAll("[data-ability-favored]").forEach((input) => {
        input.onchange = () => api().ability_save(
            Number(input.dataset.abilityFavored), null, input.checked);
    });
    $("character-detail").querySelectorAll("[data-ability-remove]").forEach((btn) => {
        btn.onclick = async () => {
            await api().ability_remove(Number(btn.dataset.abilityRemove));
            pickCharacter(c.id);
        };
    });
    $("ability-remove-mode").onchange = (event) => {
        $("character-detail").querySelectorAll("[data-ability-remove]").forEach((btn) => {
            btn.hidden = !event.target.checked;
        });
    };
    $("ability-new-add").onclick = async () => {
        const name = $("ability-new-name").value.trim();
        if (!name) return;
        await api().ability_add(c.id, name);
        pickCharacter(c.id);
    };
    $("charm-add-open").onclick = () => startPickingCharms(c);
    wireKnownCharmRows(c);

    $("character-detail").querySelectorAll("[data-row-table]").forEach((input) => {
        input.onchange = () => saveRow(input.dataset.rowTable,
            Number(input.dataset.rowId), input.dataset.rowField, input.value);
    });
    $("character-detail").querySelectorAll("[data-row-remove]").forEach((btn) => {
        btn.onclick = () => removeRow(btn.dataset.rowRemove);
    });
    $("character-detail").querySelectorAll("[data-add]").forEach((btn) => {
        btn.onclick = () => addRow(btn.dataset.add);
    });
}

/* Merits, Weapons, Armor, Intimacies, and Inventory are all the same shape
   of table - rows a character owns freely - so one save/remove/add trio
   drives all five, keyed by which table a cell or button names itself. */
const ROW_API = {
    merits: { save: "merit_save", remove: "merit_remove", add: "merit_add" },
    weapons: { save: "weapon_save", remove: "weapon_remove", add: "weapon_add" },
    armor: { save: "armor_save", remove: "armor_remove", add: "armor_add" },
    intimacies: { save: "intimacy_save", remove: "intimacy_remove", add: "intimacy_add" },
    inventory: { save: "inventory_save", remove: "inventory_remove", add: "inventory_add" },
    charms_known_manual: { save: "character_save_manual_charm", add: "character_add_manual_charm" },
    experience_purchases: { save: "experience_purchase_save", remove: "experience_purchase_remove",
                            add: "experience_purchase_add" },
};

async function saveRow(table, id, field, value) {
    await api()[ROW_API[table].save](id, { [field]: value });
}

async function removeRow(key) {
    const [table, idText] = key.split(":");
    if (!confirm("Remove this row?")) return;
    await api()[ROW_API[table].remove](Number(idText));
    pickCharacter(state.character.id);
}

async function addRow(table) {
    await api()[ROW_API[table].add](state.character.id);
    await pickCharacter(state.character.id);
    // The new row is the last one - its own section is open (this is a
    // re-render, so the state-restore above already saw to that) - so put
    // the cursor straight into it rather than making the click reach again.
    const tbody = document.querySelector(`tbody[data-rows="${table}"]`);
    const firstInput = tbody && tbody.querySelector("tr:last-child input");
    if (firstInput) firstInput.focus();
}

/* Drag-and-drop reordering plus a right-click View/Delete menu for the
   Charms Known table. Native HTML5 drag-and-drop: dragstart marks the row
   being moved, dragover on another row decides which side of it to drop on,
   drop commits the DOM move and then tells the server the new order. */
function wireKnownCharmRows(c) {
    const body = $("known-charm-rows");
    let dragging = null;

    body.querySelectorAll("tr[draggable]").forEach((row) => {
        row.ondragstart = () => { dragging = row; row.classList.add("dragging-row"); };
        row.ondragend = () => { dragging = null; row.classList.remove("dragging-row"); };
        row.ondragover = (event) => {
            event.preventDefault();
            if (!dragging || dragging === row) return;
            const before = event.clientY < row.getBoundingClientRect().top
                + row.getBoundingClientRect().height / 2;
            row.parentNode.insertBefore(dragging, before ? row : row.nextSibling);
        };
        row.ondrop = async (event) => {
            event.preventDefault();
            const order = Array.from(body.querySelectorAll("tr[draggable]"))
                .map((tr) => Number(tr.dataset.id));
            await api().character_reorder_charms(c.id, order);
        };
        row.oncontextmenu = (event) => {
            event.preventDefault();
            const menu = $("charm-known-menu");
            menu.dataset.linkId = row.dataset.id;
            menu.dataset.charmId = row.dataset.charmId;
            menu.style.left = `${event.clientX}px`;
            menu.style.top = `${event.clientY}px`;
            menu.classList.remove("hidden");
        };
    });
}

async function runKnownCharmAction(what) {
    const menu = $("charm-known-menu");
    const linkId = Number(menu.dataset.linkId);
    const kc = (state.character.charms || []).find((row) => row.id === linkId);
    if (!kc) return;
    if (what === "view") {
        showCharmView(kc);
    } else if (what === "delete") {
        if (!confirm(`Remove ${kc.name} from ${state.character.name}?`)) return;
        await api().character_remove_charm(linkId);
        pickCharacter(state.character.id);
    }
}

function showCharmView(kc) {
    const field = (label, value) => `<dt>${label}</dt><dd>${escape(value || "None")}</dd>`;
    if (kc.manual) {
        $("charm-view-body").innerHTML = `
            <h2>${escape(kc.name)}</h2>
            <dl>
                ${field("Cost", kc.cost)}
                ${field("Type", kc.type)}
            </dl>
            <textarea id="charm-view-text" style="width:100%;min-height:200px;padding:8px;
                margin-top:16px;border:1px solid var(--rule);border-radius:var(--radius);
                font-family:var(--read);font-size:1.14rem;resize:vertical">${escape(kc.text)}</textarea>
            <div class="head" style="font:12px var(--mono);color:var(--muted);margin-top:8px">
                Typed in by hand - not linked to the Charm library.</div>`;
        $("charm-view-text").onchange = (e) => {
            kc.text = e.target.value;
            api().character_save_manual_charm(kc.id, { text: kc.text });
        };
        $("charm-view").showModal();
        return;
    }
    const body = kc.simple_text && kc.simple_text.trim() ? kc.simple_text : kc.text;
    $("charm-view-body").innerHTML = `
        <h2>${escape(kc.name)}</h2>
        <dl>
            ${field("Cost", kc.cost)}
            ${field("Mins", kc.mins)}
            ${field("Type", kc.type)}
            ${field("Keywords", kc.keywords)}
            ${field("Duration", kc.duration)}
            ${field("Prerequisite Charms", kc.prereqs)}
        </dl>
        <p style="font-family:var(--read);font-size:1.14rem;margin-top:16px">${escape(body)}</p>
        <div class="head" style="font:12px var(--mono);color:var(--muted);margin-top:18px">
            ${escape(prettyBook(kc.book))} · page ${kc.page}</div>`;
    $("charm-view").showModal();
}

async function saveCharacterField(field, value) {
    const c = state.character;
    c[field] = value;
    await api().character_save(c.id, { [field]: value });
    if (field === "name" || field === "caste" || field === "concept") {
        const row = state.characters.find((r) => r.id === c.id);
        if (row) row[field] = value;
        renderCharacterRows();
    }
}

async function newCharacter() {
    const c = await api().character_new();
    await loadCharacters(true);
    pickCharacter(c.id);
}

async function deleteCharacter() {
    if (!state.character) return;
    if (!confirm(`Delete ${state.character.name}? This cannot be undone.`)) return;
    await api().character_delete(state.character.id);
    state.character = null;
    await loadCharacters(true);
    if (!state.characters.length) $("character-detail").innerHTML = "";
}

/* Adding a Charm to a character happens from the Charm Library itself, not a
   name search on the sheet: Charm names are hard to recall, but the library's
   Book/Tree/Type/Keyword/Essence filters make one easy to find without one. */
function startPickingCharms(character) {
    state.pickingCharmsFor = { id: character.id, name: character.name };
    showView("library");
    updatePickingBanner();
}

function stopPickingCharms() {
    state.pickingCharmsFor = null;
    updatePickingBanner();
    showView("characters");
    if (state.character) pickCharacter(state.character.id);
}

function updatePickingBanner() {
    const picking = state.pickingCharmsFor;
    $("charm-picking-banner").classList.toggle("hidden", !picking);
    $("charm-picking-add").classList.toggle("hidden", !picking);
    if (picking) $("charm-picking-label").textContent = `Adding Charms to ${picking.name}`;
}

/* ------------------------------------------------------------ the book menu */

function showBookMenu(event, card) {
    event.preventDefault();
    const menu = $("book-menu");
    menu.dataset.id = card.dataset.id;
    menu.dataset.title = card.dataset.title;
    // Finding the PDF is only offered for a book whose PDF is not where the
    // index says. It is the one thing that cannot be fixed any other way.
    menu.querySelector('[data-do="find"]')
        .classList.toggle("hidden", card.dataset.found === "1");
    menu.style.left = `${event.clientX}px`;
    menu.style.top = `${event.clientY}px`;
    menu.classList.remove("hidden");
}

/* A menu closes on anything that is not the menu: either mouse button, a key,
   a scroll, a resize, or the window losing focus. mousedown rather than click,
   so it goes on the way down and never outlives what the user did next. */
function closeMenus(event) {
    if (event && event.target && event.target.closest(".menu")) return;
    document.querySelectorAll(".menu").forEach((menu) =>
        menu.classList.add("hidden"));
}

["mousedown", "contextmenu", "wheel"].forEach((name) =>
    document.addEventListener(name, closeMenus, true));
document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeMenus();
});
window.addEventListener("blur", () => closeMenus());
window.addEventListener("resize", () => closeMenus());

/* The name lives in the collection file, so only the open one can be renamed. */
async function renameCollection(isOpen) {
    if (isOpen !== "1") {
        say("Open that collection first, then rename it.");
        return;
    }
    const name = await askLine("Name for this collection:",
                               $("collection").textContent);
    if (!name) return;
    const out = await api().rename_collection(name);
    if (!out.ok) { say(out.message); return; }
    $("collection").textContent = out.collection;
    drawCollections();
    say(`Renamed to ${out.collection}.`);
}

async function deleteCollection(path) {
    if (!confirm("Delete this collection?\n\n"
                + "This removes the index only. The PDFs stay where they are."))
        return;
    const out = await api().delete_collection(path);
    say(out.message);
    if (out.ok) drawCollections();
}

async function runBookAction(what) {
    const menu = $("book-menu");
    const id = Number(menu.dataset.id);
    const title = menu.dataset.title;
    if (what === "rename") {
        const name = await askLine(`Name for ${title}:`, title);
        if (!name) return;
        const out = await api().rename_book(id, name);
        if (out.ok) drawShelf(out.books, $("collection").textContent);
        else say(out.message);
    } else if (what === "find") {
        const out = await api().find_pdf(id);
        if (out.message) say(out.message);
        if (out.ok) drawShelf(out.books, $("collection").textContent);
    } else if (what === "reimport") {
        if (!confirm(`Rebuild ${title} from its PDF?\n\n`
                     + "The new sections are written before the old ones go, so "
                     + "a failure leaves the collection as it was.")) return;
        const out = await api().reimport_book(id);
        if (!out.started) { say(out.message || "Cannot reimport."); return; }
        say(`Rebuilding ${out.name}…`);
    } else if (what === "remove") {
        if (!confirm(`Take ${title} out of this collection?\n\n`
                     + "Its sections stop being searchable. The PDF is not deleted."))
            return;
        const out = await api().remove_book(id);
        say(out.message);
        if (out.ok) drawShelf(out.books, $("collection").textContent);
    } else if (what === "add") {
        const out = await api().add_book("");
        if (!out.started) { if (out.message) say(out.message); return; }
        say(`Adding ${out.name}…`);
    }
}

/* --------------------------------------------------------------- the key */

async function askForKey() {
    const box = $("key-box");
    box.returnValue = "";
    $("key-value").value = "";
    box.showModal();
    await new Promise((done) => { box.onclose = done; });

    if (box.returnValue === "drop") {
        const out = await api().drop_key(true);
        state.hasKey = out.has_key;
        say(out.message);
        return;
    }
    if (box.returnValue !== "ok") return;

    const key = $("key-value").value;
    const save = $("key-save").checked;
    say("Checking the key…");
    let out = await api().check_key(key, save, false);
    if (!out.ok && out.shape) {
        if (!confirm(`${out.message}\n\nTry it anyway?`)) { say(""); return; }
        say("Checking the key…");
        out = await api().check_key(key, save, true);
    }
    state.hasKey = !!out.has_key;
    say(out.message);
}

/* ------------------------------------------------------------ text scaling */

/* Kept in localStorage - a per-viewer preference, not app state - so it
   survives a restart instead of resetting to the default every launch. */
function loadScale() {
    try {
        const saved = parseInt(localStorage.getItem("textScale"), 10);
        return Number.isFinite(saved) ? Math.max(-3, Math.min(6, saved)) : 0;
    } catch {
        return 0;
    }
}

let scale = loadScale();

function scaleText(step) {
    scale = Math.max(-3, Math.min(6, scale + step));
    document.documentElement.style.fontSize = `${14 + scale}px`;
    try {
        localStorage.setItem("textScale", String(scale));
    } catch {
        /* no storage available; the choice just does not stick */
    }
}

/* ---------------------------------------------------------- a one line ask */

/* WebView2 does not answer window.prompt, so the page carries its own. */
function askLine(prompt, value) {
    return new Promise((done) => {
        const box = $("ask-line");
        $("ask-line-prompt").textContent = prompt;
        $("ask-line-value").value = value || "";
        box.onclose = () => done(box.returnValue === "ok"
            ? $("ask-line-value").value.trim() : null);
        box.showModal();
        $("ask-line-value").select();
    });
}

/* ---------------------------------------------------------- the bookmarks */

const bm = { path: "", entries: [], pages: 0, labels: {}, picked: [] };
const MAX_LEVEL = 4;

function pageName(index) {
    return bm.labels[String(index)] || String(index + 1);
}

function drawBookmarks() {
    $("bm-rows").innerHTML = bm.entries.map((entry, i) => {
        const level = Math.max(1, Math.min(entry.level, MAX_LEVEL));
        return `<tr data-i="${i}" class="${bm.picked.includes(i) ? "on" : ""}">
            <td style="padding-left:${10 + (level - 1) * 26}px;
                       font-weight:${level === 1 ? 600 : 400};
                       color:${level > 2 ? "var(--muted)" : "inherit"}">
                ${escape(entry.title)}</td>
            <td class="num">${escape(pageName(entry.page))}</td>
        </tr>`;
    }).join("");
    $("bm-rows").querySelectorAll("tr").forEach((tr) => {
        tr.onclick = (event) => pickBookmark(Number(tr.dataset.i), event);
    });
}

/* Shift takes a range, Control adds one. The same two keys as any list. */
function pickBookmark(i, event) {
    if (event && event.shiftKey && bm.picked.length) {
        const from = bm.picked[0];
        const [low, high] = from < i ? [from, i] : [i, from];
        bm.picked = [];
        for (let n = low; n <= high; n += 1) bm.picked.push(n);
    } else if (event && (event.ctrlKey || event.metaKey)) {
        const at = bm.picked.indexOf(i);
        if (at >= 0) bm.picked.splice(at, 1); else bm.picked.push(i);
    } else {
        bm.picked = [i];
    }
    drawBookmarks();
    const entry = bm.entries[bm.picked[0]];
    say(bm.picked.length > 1 ? `${bm.picked.length} entries selected.`
        : entry ? `page ${pageName(entry.page)}  ·  ${entry.title}` : "");
}

function sorted() { return [...bm.picked].sort((a, b) => a - b); }

async function openBookmarkPdf() {
    const path = await api().pick_pdf();
    if (!path) return;
    bm.path = path;
    $("bm-path").value = path;
    readBookmarks();
}

async function readBookmarks() {
    if (!bm.path) { say("Open a PDF first."); return; }
    const out = await api().read_bookmarks(bm.path);
    if (!out.ok) { say(out.message); return; }
    bm.entries = out.entries;
    bm.pages = out.pages;
    bm.labels = out.labels;
    bm.picked = [];
    drawBookmarks();
    say(`${out.name}: ${out.pages} pages, ${out.entries.length} bookmarks.`);
}

async function readContents() {
    if (!bm.path) { say("Open a PDF first."); return; }
    say("Reading the contents page…");
    const out = await api().read_contents(bm.path, $("bm-pages").value);
    if (!out.ok) { say(out.message); return; }
    bm.entries = out.entries;
    bm.pages = out.pages;
    bm.picked = [];
    drawBookmarks();
    say(out.message);
}

/* Rows move from the top down: the ceiling of a row comes from the row above
   it, and that row must find its new level first. */
function shiftLevel(step) {
    sorted().forEach((i) => {
        const ceiling = i === 0 ? 1 : bm.entries[i - 1].level + 1;
        const level = bm.entries[i].level + step;
        bm.entries[i].level = Math.max(1, Math.min(level, ceiling, MAX_LEVEL));
    });
    drawBookmarks();
}

function wireBookmarks() {
    $("bm-open").onclick = openBookmarkPdf;
    $("bm-read").onclick = readBookmarks;
    $("bm-parse").onclick = readContents;
    $("bm-out").onclick = () => shiftLevel(-1);
    $("bm-in").onclick = () => shiftLevel(1);
    $("bm-rename").onclick = async () => {
        const i = bm.picked[0];
        if (i === undefined) return;
        const name = await askLine("Title:", bm.entries[i].title);
        if (name) { bm.entries[i].title = name; drawBookmarks(); }
    };
    $("bm-page").onclick = async () => {
        const i = bm.picked[0];
        if (i === undefined) return;
        const value = await askLine("Page, as your reader shows it:",
                                    pageName(bm.entries[i].page));
        if (!value) return;
        const label = Object.keys(bm.labels).find((k) => bm.labels[k] === value);
        if (label !== undefined) bm.entries[i].page = Number(label);
        else {
            const page = Number(value);
            if (!page || page < 1 || page > bm.pages) {
                say(`That PDF has ${bm.pages} pages.`); return;
            }
            bm.entries[i].page = page - 1;
        }
        drawBookmarks();
    };
    $("bm-add").onclick = async () => {
        const name = await askLine("Title:", "");
        if (!name) return;
        const i = bm.picked[0];
        const near = i === undefined ? null : bm.entries[i];
        const at = i === undefined ? bm.entries.length : i + 1;
        bm.entries.splice(at, 0, { level: near ? near.level : 1, title: name,
                                   page: near ? near.page : 0 });
        bm.picked = [at];
        drawBookmarks();
    };
    $("bm-del").onclick = () => {
        sorted().reverse().forEach((i) => bm.entries.splice(i, 1));
        bm.picked = [];
        drawBookmarks();
    };
    $("bm-save").onclick = async () => {
        if (!bm.entries.length) { say("Nothing to save."); return; }
        if (!confirm(`Write ${bm.entries.length} bookmarks into:\n${bm.path}`
                     + "\n\nThe file is changed in place.")) return;
        $("bm-save").disabled = true;
        say("Saving. Do not open the PDF until this finishes…");
        const out = await api().save_bookmarks(bm.path, bm.entries);
        $("bm-save").disabled = false;
        say(out.message);
    };
}

/* -------------------------------------------------------- the folder import */

const im = { folder: "", results: [] };

function wireImport() {
    $("im-browse").onclick = async () => {
        const folder = await api().pick_folder();
        if (!folder) return;
        im.folder = folder;
        $("im-folder").value = folder;
        checkFolder();
    };
    $("im-check").onclick = checkFolder;
    $("im-index").onclick = async () => {
        const good = im.results.filter((r) => r.ok).map((r) => r.path);
        const out = await api().index_folder(good, $("im-name").value);
        if (!out.started) { say(out.message); return; }
        $("im-index").disabled = true;
        say(`Building ${out.target}…`);
    };
}

async function checkFolder() {
    if (!im.folder) { say("Pick a folder first."); return; }
    $("im-check").disabled = true;
    say("Checking…");
    const out = await api().check_folder(im.folder, $("im-deep").checked);
    $("im-check").disabled = false;
    if (!out.ok) { say(out.message); return; }
    im.results = out.results;
    if (!$("im-name").value) $("im-name").value = out.name;
    const good = im.results.filter((r) => r.ok).length;
    $("im-rows").innerHTML = im.results.map((r) => `
        <tr>
            <td>${escape(r.name)}</td>
            <td class="num">${r.pages || ""}</td>
            <td style="color:${r.ok ? "inherit" : "var(--warn)"}">
                ${r.ok ? "Ready" : escape(r.reason)}</td>
        </tr>`).join("");
    $("im-count").textContent =
        `${im.results.length} PDFs · ${good} ready · ${im.results.length - good} cannot`;
    $("im-index").disabled = good === 0;
    say(good ? "Name the collection, then index." : "No book here can be indexed.");
}

/* ------------------------------------------------------- column resizing */

const MIN_COLUMN = 56;

/* A grip on the right edge of every heading but the last.

   The table lays out fixed, so the first drag freezes every column at the
   width it already has. Without that the columns are still percentages, and
   moving one would shift the others as well. */
function makeResizable(table) {
    const heads = Array.from(table.querySelectorAll("thead th"));
    heads.slice(0, -1).forEach((th, i) => {
        const grip = document.createElement("span");
        grip.className = "grip";
        th.appendChild(grip);
        grip.onmousedown = (event) => {
            event.preventDefault();
            event.stopPropagation();
            heads.forEach((other) => {
                other.style.width = `${other.getBoundingClientRect().width}px`;
            });
            const startX = event.clientX;
            const startW = th.getBoundingClientRect().width;
            const next = heads[i + 1];
            const nextW = next.getBoundingClientRect().width;
            const move = (e) => {
                // What one column takes, the one beside it gives up, so the
                // table keeps its width and nothing overflows the pane.
                let by = e.clientX - startX;
                by = Math.max(MIN_COLUMN - startW, Math.min(nextW - MIN_COLUMN, by));
                th.style.width = `${startW + by}px`;
                next.style.width = `${nextW - by}px`;
            };
            const stop = () => {
                document.removeEventListener("mousemove", move);
                document.removeEventListener("mouseup", stop);
                document.body.classList.remove("dragging");
            };
            document.body.classList.add("dragging");
            document.addEventListener("mousemove", move);
            document.addEventListener("mouseup", stop);
        };
    });
}

/* --------------------------------------------------------------- the sash */

function dragSash(event) {
    event.preventDefault();
    const panes = $("panes");
    const move = (e) => {
        const box = panes.getBoundingClientRect();
        const share = ((e.clientX - box.left) / box.width) * 100;
        // The clamp is the only limit. No widget gets a say.
        document.documentElement.style.setProperty(
            "--split", `${Math.min(80, Math.max(20, share))}%`);
    };
    const stop = () => {
        document.removeEventListener("mousemove", move);
        document.removeEventListener("mouseup", stop);
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", stop);
}

/* --------------------------------------------------------------- start up */

function wire() {
    $("search-form").onsubmit = runSearch;
    $("open-pdf").onclick = openPdf;
    $("charm-form").onsubmit = runCharmSearch;
    $("charm-build").onclick = buildCharms;
    $("char-new").onclick = newCharacter;
    $("char-delete").onclick = deleteCharacter;
    $("charm-picking-done").onclick = stopPickingCharms;
    $("charm-picking-add").onclick = async () => {
        if (!state.pickingCharmsFor || !state.charm) return;
        await api().character_add_charm(state.pickingCharmsFor.id, state.charm.id);
        say(`Added ${state.charm.name} to ${state.pickingCharmsFor.name}.`);
    };
    $("charm-open").onclick = () => {
        if (!state.charm) return;
        api().open_pdf(state.charm.source || "", state.charm.page)
             .then((out) => say(out.message));
    };
    $("charm-clear").onclick = () => {
        $("charm-terms").value = "";
        ["f-book", "f-tree", "f-type", "f-keyword", "f-essence"]
            .forEach((id) => { $(id).value = ""; });
        runCharmSearch();
    };
    $("ask-new").onclick = () => askQuestion(false);
    $("ask-more").onclick = () => askQuestion(true);
    $("ask-clear").onclick = async () => {
        await api().clear();
        $("transcript").innerHTML = "";
        $("ask-sources").innerHTML = "";
        $("excerpt").innerHTML = "";
        say("Cleared.");
    };
    $("question").onkeydown = (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            askQuestion($("transcript").children.length > 0);
        }
    };
    $("shelf-toggle").onclick = () => setShelfClosed(!$("shelf").classList.contains("closed"));
    document.querySelectorAll(".tab[data-view]").forEach((tab) => {
        tab.onclick = () => showView(tab.dataset.view);
    });
    // mousedown already ran closeMenus() by the time this click fires, so the
    // menu just opens fresh each click; clicking anywhere else closes it.
    $("exalted-menu-btn").onclick = () => {
        const rect = $("exalted-menu-btn").getBoundingClientRect();
        const menu = $("exalted-menu");
        menu.style.left = `${rect.left}px`;
        menu.style.top = `${rect.bottom}px`;
        menu.classList.remove("hidden");
    };
    $("exalted-menu").querySelectorAll("button[data-view]").forEach((item) => {
        item.onclick = () => {
            closeMenus();
            showView(item.dataset.view);
        };
    });
    $("sash").onmousedown = dragSash;
    $("key-btn").onclick = askForKey;
    // Neither button submits the form (that's the primary "Use this key"
    // button's job, so Enter in the field reaches it), so these close the
    // dialog by hand with the return value the code below expects.
    $("key-cancel").onclick = () => $("key-box").close("");
    $("key-drop").onclick = () => $("key-box").close("drop");
    $("ask-line-cancel").onclick = () => $("ask-line").close("");
    $("text-bigger").onclick = () => scaleText(1);
    $("text-smaller").onclick = () => scaleText(-1);
    document.addEventListener("keydown", (event) => {
        if (!event.ctrlKey) return;
        if (event.key === "+" || event.key === "=") scaleText(1);
        else if (event.key === "-") scaleText(-1);
        else if (event.key === "0") { scale = 0; scaleText(0); }
    });
    // The menu holds what the action needs in its dataset, so closing it first
    // is safe and the menu never sits open behind a dialog.
    $("book-menu").querySelectorAll("button").forEach((item) => {
        item.onclick = () => {
            const what = item.dataset.do;
            closeMenus();
            runBookAction(what);
        };
    });
    $("charm-known-menu").querySelectorAll("button").forEach((item) => {
        item.onclick = () => {
            const what = item.dataset.do;
            closeMenus();
            runKnownCharmAction(what);
        };
    });
    $("collection-menu").querySelectorAll("button").forEach((item) => {
        item.onclick = () => {
            const menu = $("collection-menu");
            const path = menu.dataset.path;
            const open = menu.dataset.open;
            closeMenus();
            if (item.dataset.do === "delete-collection") deleteCollection(path);
            else renameCollection(open);
        };
    });
    document.querySelectorAll("table.rows").forEach(makeResizable);
    document.querySelectorAll("#library-table th.sortable").forEach((th) => {
        th.onclick = () => sortCharms(th.dataset.sort);
    });
    wireBookmarks();
    wireImport();
}

async function boot() {
    wire();
    scaleText(0);      // apply whatever text size was saved last launch
    setInterval(pump, 150);
    const start = await api().state();
    drawShelf(start.books, start.collection);
    say(`${start.collection} · ${start.books.length} books`
        + (start.charms ? ` · ${start.charms} charms` : ""));
    $("terms").focus();
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
} else {
    boot();
}
