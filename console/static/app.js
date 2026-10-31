// Console Tesoreria — piccole interazioni (nessuno script inline: CSP script-src 'self').
document.addEventListener("DOMContentLoaded", () => {
  // seleziona tutti
  document.querySelectorAll("[data-seleziona-tutti]").forEach((c) => {
    c.addEventListener("change", () => {
      c.closest("table").querySelectorAll('tbody input[type="checkbox"]').forEach((x) => { x.checked = c.checked; });
    });
  });
  // torna indietro
  document.querySelectorAll("[data-indietro]").forEach((a) => {
    a.addEventListener("click", (e) => { if (history.length > 1) { e.preventDefault(); history.back(); } });
  });
  // attesa durante la simulazione
  document.querySelectorAll("form[data-attesa]").forEach((f) => {
    f.addEventListener("submit", () => f.classList.add("attesa"));
  });
  // grafici del margine
  const eur = (v) => new Intl.NumberFormat("it-IT", { maximumFractionDigits: 0 }).format(v) + " €";
  document.querySelectorAll("canvas[data-grafico]").forEach((cv) => {
    if (!window.Chart) return;
    const d = JSON.parse(cv.dataset.grafico);
    const sets = [{ label: d.valori2 ? "Simulazione" : "Margine sul fido", data: d.valori, borderColor: "#1f4e79",
      backgroundColor: "rgba(31,78,121,.08)", fill: true, tension: .15, pointRadius: 0, borderWidth: 2 }];
    if (d.valori2) sets.push({ label: "Piano ufficiale", data: d.valori2, borderColor: "#9a9a92", borderDash: [5, 4],
      pointRadius: 0, borderWidth: 1.5, fill: false, tension: .15 });
    if (d.soglia) sets.push({ label: "Soglia di allerta", data: d.valori.map(() => d.soglia), borderColor: "#9a6b00",
      borderWidth: 1, pointRadius: 0, borderDash: [2, 3], fill: false });
    sets.push({ label: "Fido", data: d.valori.map(() => 0), borderColor: "#b3261e", borderWidth: 1, pointRadius: 0, fill: false });
    new Chart(cv, {
      type: "line", data: { labels: d.etichette, datasets: sets },
      options: { responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
        plugins: { legend: { position: "bottom", labels: { boxWidth: 12 } },
          tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${eur(c.parsed.y)}` } } },
        scales: { y: { ticks: { callback: (v) => eur(v) } }, x: { ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 14 } } } },
    });
  });
});
