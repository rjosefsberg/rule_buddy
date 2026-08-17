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
    document.querySelectorAll(".view").forEach((view) =>
        view.classList.toggle("hidden", view.dataset.view !== name));
    if (name === "library") loadFilters();
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
    document.querySelectorAll("#results tr").forEach((tr) =>
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
    $("shelf-toggle").onclick = () => {
        const shelf = $("shelf");
        shelf.classList.toggle("closed");
        $("shelf-toggle").textContent = shelf.classList.contains("closed") ? "›" : "‹";
    };
    document.querySelectorAll(".tab").forEach((tab) => {
        tab.onclick = () => showView(tab.dataset.view);
    });
    $("sash").onmousedown = dragSash;
}

window.addEventListener("pywebviewready", async () => {
    wire();
    const start = await api().state();
    drawShelf(start.books, start.collection);
    say(`${start.collection} · ${start.books.length} books`
        + (start.charms ? ` · ${start.charms} charms` : ""));
    $("terms").focus();
});
