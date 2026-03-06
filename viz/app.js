(() => {
  const ID_KEYS = ["id", "example_id", "uid", "uuid", "record_id"];
  const QUESTION_KEYS = ["question", "prompt", "input", "query", "instruction", "question_text", "user_prompt"];
  const ANSWER_KEYS = ["answer", "response", "output", "completion", "text", "chosen", "assistant", "final", "assistant_response"];

  const state = {
    comparisons: [],
    currentIndex: 0,
    labels: {
      a: "File A",
      b: "File B"
    }
  };

  const els = {
    fileA: document.getElementById("file-a"),
    fileB: document.getElementById("file-b"),
    loadBtn: document.getElementById("load-btn"),
    prevBtn: document.getElementById("prev-btn"),
    nextBtn: document.getElementById("next-btn"),
    randomBtn: document.getElementById("random-btn"),
    jumpInput: document.getElementById("jump-input"),
    counter: document.getElementById("counter"),
    status: document.getElementById("status"),
    errors: document.getElementById("errors"),
    recordId: document.getElementById("record-id"),
    labelA: document.getElementById("label-a"),
    labelB: document.getElementById("label-b"),
    question: document.getElementById("question-content"),
    answerA: document.getElementById("answer-a"),
    answerB: document.getElementById("answer-b")
  };

  if (window.marked && typeof window.marked.setOptions === "function") {
    window.marked.setOptions({
      gfm: true,
      breaks: false,
      headerIds: false,
      mangle: false
    });
  }

  function setError(messages) {
    if (!messages || messages.length === 0) {
      els.errors.classList.remove("visible");
      els.errors.textContent = "";
      return;
    }
    const MAX_MESSAGES = 50;
    const shown = messages.slice(0, MAX_MESSAGES);
    const hiddenCount = Math.max(0, messages.length - shown.length);

    els.errors.classList.add("visible");
    els.errors.innerHTML = shown.map((msg) => `<div>${escapeHtml(msg)}</div>`).join("");
    if (hiddenCount > 0) {
      els.errors.innerHTML += `<div>... and ${hiddenCount} more warnings</div>`;
    }
  }

  function setStatus(text, isWarning = false) {
    els.status.textContent = text;
    if (isWarning) {
      els.status.style.background = "var(--warn-bg)";
      els.status.style.color = "var(--warn)";
    } else {
      els.status.style.background = "var(--accent-soft)";
      els.status.style.color = "#11443b";
    }
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function pickFirstValue(obj, keys) {
    for (const key of keys) {
      if (Object.prototype.hasOwnProperty.call(obj, key)) {
        const value = obj[key];
        if (value !== null && value !== undefined && String(value).trim() !== "") {
          return { key, value: String(value) };
        }
      }
    }
    return null;
  }

  function extractTextContent(value) {
    if (value === null || value === undefined) {
      return null;
    }
    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
      const text = String(value);
      return text.trim() ? text : null;
    }
    if (Array.isArray(value)) {
      const parts = [];
      for (const item of value) {
        const text = extractTextContent(item);
        if (text) {
          parts.push(text);
        }
      }
      if (parts.length > 0) {
        return parts.join("\n");
      }
      return null;
    }
    if (typeof value === "object") {
      // Handles chat formats like {type: "text", text: "..."} or generic nested content objects.
      if (typeof value.text === "string" && value.text.trim()) {
        return value.text;
      }
      if (typeof value.value === "string" && value.value.trim()) {
        return value.value;
      }
      if (typeof value.content === "string" && value.content.trim()) {
        return value.content;
      }
      if (Array.isArray(value.content)) {
        return extractTextContent(value.content);
      }
    }
    return null;
  }

  function extractFromChatTurns(row) {
    const turns = Array.isArray(row.conversations)
      ? row.conversations
      : Array.isArray(row.messages)
        ? row.messages
        : null;

    if (!turns) {
      return null;
    }

    let question = null;
    let answer = null;
    let firstNonAssistant = null;
    let lastAnyText = null;

    for (const turn of turns) {
      if (!turn || typeof turn !== "object") {
        continue;
      }

      const roleRaw = turn.from ?? turn.role ?? turn.speaker ?? turn.author;
      const role = typeof roleRaw === "string" ? roleRaw.toLowerCase() : "";
      const text = extractTextContent(turn.value ?? turn.content ?? turn.text ?? turn.message);
      if (!text) {
        continue;
      }
      lastAnyText = text;

      if (!firstNonAssistant && role !== "assistant" && role !== "model" && role !== "gpt") {
        firstNonAssistant = text;
      }

      if (!question && (role === "user" || role === "human")) {
        question = text;
      }
      if (role === "assistant" || role === "model" || role === "gpt") {
        // Use the latest assistant response if multiple exist.
        answer = text;
      }
    }

    if (!question && firstNonAssistant) {
      question = firstNonAssistant;
    }
    if (!answer && lastAnyText) {
      answer = lastAnyText;
    }

    if (!question || !answer) {
      return null;
    }

    return { question, answer };
  }

  async function parseJsonlFile(file, sideLabel) {
    const text = await file.text();
    const lines = text.split(/\r?\n/);
    const recordsById = new Map();
    const errors = [];
    const duplicateSamples = [];
    let missingIdCount = 0;
    let missingQuestionCount = 0;
    let missingAnswerCount = 0;
    let duplicateIdCount = 0;

    for (let i = 0; i < lines.length; i += 1) {
      const raw = lines[i].trim();
      if (!raw) {
        continue;
      }

      let row;
      try {
        row = JSON.parse(raw);
      } catch (err) {
        errors.push(`${sideLabel}: invalid JSON at line ${i + 1}`);
        continue;
      }

      if (typeof row !== "object" || row === null || Array.isArray(row)) {
        errors.push(`${sideLabel}: line ${i + 1} must be a JSON object`);
        continue;
      }

      const idHit = pickFirstValue(row, ID_KEYS);
      if (!idHit) {
        missingIdCount += 1;
        continue;
      }

      const questionHit = pickFirstValue(row, QUESTION_KEYS);
      const answerHit = pickFirstValue(row, ANSWER_KEYS);
      const chatExtract = (!questionHit || !answerHit) ? extractFromChatTurns(row) : null;

      const question = questionHit ? questionHit.value : chatExtract ? chatExtract.question : null;
      const answer = answerHit ? answerHit.value : chatExtract ? chatExtract.answer : null;

      if (!question) {
        missingQuestionCount += 1;
        continue;
      }
      if (!answer) {
        missingAnswerCount += 1;
        continue;
      }

      const id = idHit.value;
      if (recordsById.has(id)) {
        duplicateIdCount += 1;
        if (duplicateSamples.length < 10) {
          duplicateSamples.push(`${id} (line ${i + 1})`);
        }
        continue;
      }

      recordsById.set(id, {
        id,
        question,
        answer,
        sourceLine: i + 1
      });
    }

    return {
      recordsById,
      errors,
      stats: {
        missingIdCount,
        missingQuestionCount,
        missingAnswerCount,
        duplicateIdCount,
        duplicateSamples,
        parsedCount: recordsById.size
      }
    };
  }

  function toSafeMarkdownHtml(markdown) {
    const parser = window.marked;
    const sanitizer = window.DOMPurify;

    if (!parser || typeof parser.parse !== "function") {
      return escapeHtml(markdown);
    }

    const dirtyHtml = parser.parse(markdown || "");

    if (!sanitizer || typeof sanitizer.sanitize !== "function") {
      return dirtyHtml;
    }

    return sanitizer.sanitize(dirtyHtml, {
      USE_PROFILES: { html: true },
      ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto|tel|data):|[^a-z]|[a-z+.-]+(?:[^a-z+.-:]|$))/i
    });
  }

  function setMarkdown(el, markdown) {
    const value = markdown || "";
    if (value.trim() === "") {
      el.classList.add("empty");
      el.textContent = "Empty content.";
      return;
    }

    el.classList.remove("empty");
    el.innerHTML = toSafeMarkdownHtml(value);
  }

  function setNavigationEnabled(enabled) {
    els.prevBtn.disabled = !enabled;
    els.nextBtn.disabled = !enabled;
    els.randomBtn.disabled = !enabled;
    els.jumpInput.disabled = !enabled;
    if (!enabled) {
      els.jumpInput.value = "";
    }
  }

  function renderCurrent() {
    const total = state.comparisons.length;
    if (!total) {
      els.counter.textContent = "0 / 0";
      els.recordId.textContent = "ID: -";
      els.question.classList.add("empty");
      els.answerA.classList.add("empty");
      els.answerB.classList.add("empty");
      els.question.textContent = "No record loaded.";
      els.answerA.textContent = "No record loaded.";
      els.answerB.textContent = "No record loaded.";
      setNavigationEnabled(false);
      return;
    }

    if (state.currentIndex < 0) {
      state.currentIndex = 0;
    }
    if (state.currentIndex >= total) {
      state.currentIndex = total - 1;
    }

    const record = state.comparisons[state.currentIndex];

    els.counter.textContent = `${state.currentIndex + 1} / ${total}`;
    els.recordId.textContent = `ID: ${record.id}`;
    els.jumpInput.value = String(state.currentIndex + 1);

    setMarkdown(els.question, record.question);
    setMarkdown(els.answerA, record.answerA);
    setMarkdown(els.answerB, record.answerB);

    setNavigationEnabled(true);
    els.prevBtn.disabled = state.currentIndex <= 0;
    els.nextBtn.disabled = state.currentIndex >= total - 1;
  }

  function buildComparisons(parsedA, parsedB) {
    const idsA = new Set(parsedA.recordsById.keys());
    const idsB = new Set(parsedB.recordsById.keys());

    const sharedIds = [...idsA].filter((id) => idsB.has(id)).sort((x, y) => x.localeCompare(y));

    const comparisons = sharedIds.map((id) => {
      const a = parsedA.recordsById.get(id);
      const b = parsedB.recordsById.get(id);

      return {
        id,
        question: a.question,
        answerA: a.answer,
        answerB: b.answer
      };
    });

    return {
      comparisons,
      unmatchedA: [...idsA].filter((id) => !idsB.has(id)).length,
      unmatchedB: [...idsB].filter((id) => !idsA.has(id)).length
    };
  }

  async function loadAndCompare() {
    const fileA = els.fileA.files?.[0];
    const fileB = els.fileB.files?.[0];

    setError([]);

    if (!fileA || !fileB) {
      setStatus("Please select both File A and File B.", true);
      return;
    }

    state.labels.a = fileA.name;
    state.labels.b = fileB.name;
    els.labelA.textContent = fileA.name;
    els.labelB.textContent = fileB.name;

    setStatus("Parsing files...");
    setNavigationEnabled(false);

    let parsedA;
    let parsedB;

    try {
      [parsedA, parsedB] = await Promise.all([
        parseJsonlFile(fileA, "File A"),
        parseJsonlFile(fileB, "File B")
      ]);
    } catch (err) {
      setStatus("Failed to read selected files.", true);
      setError([`Read error: ${err instanceof Error ? err.message : String(err)}`]);
      return;
    }

    const { comparisons, unmatchedA, unmatchedB } = buildComparisons(parsedA, parsedB);

    const warnings = [];
    warnings.push(...parsedA.errors);
    warnings.push(...parsedB.errors);

    if (parsedA.stats.missingIdCount > 0) {
      warnings.push(`File A: ${parsedA.stats.missingIdCount} rows skipped (missing id)`);
    }
    if (parsedB.stats.missingIdCount > 0) {
      warnings.push(`File B: ${parsedB.stats.missingIdCount} rows skipped (missing id)`);
    }
    if (parsedA.stats.missingQuestionCount > 0) {
      warnings.push(`File A: ${parsedA.stats.missingQuestionCount} rows skipped (missing question key)`);
    }
    if (parsedB.stats.missingQuestionCount > 0) {
      warnings.push(`File B: ${parsedB.stats.missingQuestionCount} rows skipped (missing question key)`);
    }
    if (parsedA.stats.missingAnswerCount > 0) {
      warnings.push(`File A: ${parsedA.stats.missingAnswerCount} rows skipped (missing answer key)`);
    }
    if (parsedB.stats.missingAnswerCount > 0) {
      warnings.push(`File B: ${parsedB.stats.missingAnswerCount} rows skipped (missing answer key)`);
    }
    if (parsedA.stats.duplicateIdCount > 0) {
      const sample = parsedA.stats.duplicateSamples.join(", ");
      warnings.push(`File A: ${parsedA.stats.duplicateIdCount} duplicate IDs skipped (keeping first). Examples: ${sample}`);
    }
    if (parsedB.stats.duplicateIdCount > 0) {
      const sample = parsedB.stats.duplicateSamples.join(", ");
      warnings.push(`File B: ${parsedB.stats.duplicateIdCount} duplicate IDs skipped (keeping first). Examples: ${sample}`);
    }

    state.comparisons = comparisons;
    state.currentIndex = 0;

    if (comparisons.length === 0) {
      renderCurrent();
      setStatus("No overlapping IDs found between files.", true);
      if (warnings.length) {
        setError(warnings);
      }
      return;
    }

    const statusParts = [
      `Loaded ${comparisons.length} shared records`,
      `${unmatchedA} IDs only in A`,
      `${unmatchedB} IDs only in B`
    ];

    setStatus(statusParts.join(" • "), unmatchedA > 0 || unmatchedB > 0);
    setError(warnings);
    renderCurrent();
  }

  function goToIndex(index) {
    if (!state.comparisons.length) {
      return;
    }
    state.currentIndex = Math.max(0, Math.min(index, state.comparisons.length - 1));
    renderCurrent();
  }

  function goToRandom() {
    const total = state.comparisons.length;
    if (!total) {
      return;
    }
    if (total === 1) {
      goToIndex(0);
      return;
    }

    let next = state.currentIndex;
    while (next === state.currentIndex) {
      next = Math.floor(Math.random() * total);
    }
    goToIndex(next);
  }

  els.loadBtn.addEventListener("click", () => {
    loadAndCompare();
  });

  els.prevBtn.addEventListener("click", () => {
    goToIndex(state.currentIndex - 1);
  });

  els.nextBtn.addEventListener("click", () => {
    goToIndex(state.currentIndex + 1);
  });

  els.randomBtn.addEventListener("click", () => {
    goToRandom();
  });

  els.jumpInput.addEventListener("change", () => {
    const raw = Number(els.jumpInput.value);
    if (!Number.isFinite(raw) || raw < 1) {
      els.jumpInput.value = String(state.currentIndex + 1);
      return;
    }
    goToIndex(Math.floor(raw) - 1);
  });

  document.addEventListener("keydown", (event) => {
    const tag = event.target && "tagName" in event.target ? event.target.tagName : "";
    if (tag === "INPUT" || tag === "TEXTAREA") {
      return;
    }

    if (event.key === "ArrowLeft") {
      goToIndex(state.currentIndex - 1);
    } else if (event.key === "ArrowRight") {
      goToIndex(state.currentIndex + 1);
    }
  });

  renderCurrent();
})();
