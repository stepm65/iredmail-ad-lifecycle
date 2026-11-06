"use strict";
const batchTable = document.getElementById("batch-mailboxes");
if (batchTable) {
  const rows = [...batchTable.querySelectorAll("tbody tr[data-email]")];
  const boxes = [...batchTable.querySelectorAll('input[name="email"]')];
  const search = document.getElementById("batch-search");
  const status = document.getElementById("batch-status");
  const count = document.getElementById("batch-selected-count");
  const button = document.getElementById("batch-preview-button");
  const update = () => {
    const selected = boxes.filter(box => box.checked);
    const hidden = selected.filter(box => box.closest("tr").hidden).length;
    count.textContent = `Выбрано: ${selected.length}${hidden ? ` (скрыто фильтром: ${hidden})` : ""}`;
    button.disabled = selected.length < 1 || selected.length > 500;
  };
  const filter = () => {
    const query = search.value.toLocaleLowerCase();
    rows.forEach(row => {
      row.hidden = !row.dataset.email.toLocaleLowerCase().includes(query) || (status.value && row.dataset.status !== status.value);
    });
    update();
  };
  batchTable.addEventListener("change", update);
  search.addEventListener("input", filter);
  status.addEventListener("change", filter);
  document.getElementById("batch-select-visible").addEventListener("click", () => {
    boxes.forEach(box => { if (!box.disabled && !box.closest("tr").hidden) box.checked = true; });
    update();
  });
  document.getElementById("batch-clear").addEventListener("click", () => {
    boxes.forEach(box => { box.checked = false; });
    update();
  });
  update();
}
const table = document.getElementById("inventory-table");
if (table) {
  const boxes = [...table.querySelectorAll('input[name="email"]')];
  const count = document.getElementById("selected-count");
  const button = document.getElementById("preview-button");
  table.addEventListener("change", () => {
    const n = boxes.filter(box => box.checked).length;
    count.textContent = `Выбрано: ${n}`;
    button.disabled = n < 1 || n > 500;
  });
  document.getElementById("inventory-search").addEventListener("input", event => {
    const query = event.target.value.toLocaleLowerCase();
    table.querySelectorAll("tbody tr[data-email]").forEach(row => {
      row.hidden = !row.dataset.email.toLocaleLowerCase().includes(query);
    });
  });
}
const poll = document.getElementById("poll");
if (poll) {
  const timer = setInterval(async () => {
    try {
      const response = await fetch(poll.dataset.url, {redirect: "error", cache: "no-store"});
      if (!response.ok) { clearInterval(timer); return; }
      const result = await response.json();
      if (!["queued", "running"].includes(result.status)) { clearInterval(timer); location.reload(); }
    } catch (_) { poll.textContent = "Не удалось обновить состояние. Обновите страницу вручную."; }
  }, 5000);
}
