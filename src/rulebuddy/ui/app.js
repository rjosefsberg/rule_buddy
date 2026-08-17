/* app.js - the page. Every fact comes from Python through window.pywebview.api.

   Nothing here holds state that Python already holds. The page asks, draws
   what it gets, and asks again. */

const $ = (id) => document.getElementById(id);
const api = () => window.pywebview.api;

const state = {
    view: "search",
    pick: null,          // the chosen section id
    charm: null,         // the chosen charm row
    charms: [],
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
    if (name === "library") loadFilters();
    if (name === "ask") $("question").focus();
    // The editor and the importer are tools, not reading. They take the width.
    const wide = name === "bookmarks" || name === "import";
    $("sash").classList.toggle("hidden", wide);
    $("right").classList.toggle("hidden", wide);
    $("left").style.flexBasis = wide ? "100%" : "";
}

/* ------------------------------------------------------------- the shelf */

function drawShelf(books, collection) {
    $("collection").textContent = collection;
    $("shelf-body").innerHTML = books.map((book) => `
        <div class="book">
            <h4>${escape(book.title)}</h4>
            <div class="meta">${book.pages} pages · ${book.chunks} sections</div>
            ${book.found ? "" : '<div class="meta missing">PDF not found</div>'}
        </div>`).join("");
    drawCollections();
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

/* Python pushes every stage of an answer through here. The page never waits
   on a call that takes a minute. */
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
    fill("f-book", filters.books);
    fill("f-tree", filters.trees);
    fill("f-type", filters.types);
    fill("f-keyword", filters.keywords);
    fill("f-essence", filters.essence.map(String));
    filtersLoaded = true;
    runCharmSearch();
}

function fill(id, values) {
    $(id).innerHTML = '<option value="">Any</option>'
        + values.map((v) => `<option>${escape(v)}</option>`).join("");
    $(id).onchange = runCharmSearch;
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
    $("charm-rows").innerHTML = rows.map((row, i) => `
        <tr data-i="${i}">
            <td>${escape(row.name)}</td>
            <td>${escape(row.tree)}</td>
            <td class="tag">${escape(row.cost)}</td>
            <td class="num">${row.essence || ""}</td>
            <td class="num">${row.page}</td>
        </tr>`).join("");
    $("charm-rows").querySelectorAll("tr").forEach((tr) => {
        tr.onclick = () => pickCharm(Number(tr.dataset.i));
    });
    say(`${rows.length} charms.`);
    if (rows.length) pickCharm(0);
    else $("charm-detail").innerHTML = "";
}

function pickCharm(i) {
    const row = state.charms[i];
    if (!row) return;
    state.charm = row;
    document.querySelectorAll("#charm-rows tr").forEach((tr) =>
        tr.classList.toggle("on", Number(tr.dataset.i) === i));
    const field = (label, value) =>
        `<dt>${label}</dt><dd>${escape(value || "None")}</dd>`;
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
        <p style="font-family:var(--read);font-size:16px;margin-top:16px">
            ${escape(row.text)}</p>
        <div class="head" style="font:12px var(--mono);color:var(--muted);
             margin-top:18px">${escape(row.book || "")} · page ${row.page}</div>`;
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
    $("shelf-toggle").onclick = () => {
        const shelf = $("shelf");
        shelf.classList.toggle("closed");
        $("shelf-toggle").textContent = shelf.classList.contains("closed") ? "›" : "‹";
    };
    document.querySelectorAll(".tab").forEach((tab) => {
        tab.onclick = () => showView(tab.dataset.view);
    });
    $("sash").onmousedown = dragSash;
    wireBookmarks();
    wireImport();
}

window.addEventListener("pywebviewready", async () => {
    wire();
    const start = await api().state();
    drawShelf(start.books, start.collection);
    say(`${start.collection} · ${start.books.length} books`
        + (start.charms ? ` · ${start.charms} charms` : ""));
    $("terms").focus();
});
