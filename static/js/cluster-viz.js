/**
 * Paper Cluster Visualization
 *
 * Renders an interactive Plotly scatter plot of papers clustered by topic
 * similarity. Lazy-loads Plotly from CDN on first use.
 */

(function () {
  "use strict";

  const PLOTLY_CDN =
    "https://cdn.plot.ly/plotly-2.35.0.min.js";

  let plotlyLoaded = false;
  let isLoading = false;

  /** Lazy-load Plotly.js from CDN */
  function ensurePlotly() {
    if (plotlyLoaded || window.Plotly) {
      plotlyLoaded = true;
      return Promise.resolve();
    }
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = PLOTLY_CDN;
      script.onload = () => {
        plotlyLoaded = true;
        resolve();
      };
      script.onerror = () => reject(new Error("Failed to load Plotly"));
      document.head.appendChild(script);
    });
  }

  /** Fetch cluster data from backend.
      scope comes from the button's data-scope: "library" clusters the
      caller's saved papers; anything else clusters the search pool. */
  async function fetchClusters() {
    const scope =
      document.getElementById("cluster-viz-btn")?.dataset.scope || "search";
    const res = await fetch(
      "/api/cluster?scope=" + encodeURIComponent(scope),
      { method: "POST" }
    );
    const data = await res.json();
    if (!data.ok) {
      throw new Error(data.error || "Clustering failed");
    }
    return data;
  }

  /** Compute dot size from citation count (log scale, clamped) */
  function dotSize(citationCount) {
    if (!citationCount || citationCount <= 0) return 10;
    // Log scale: 0 cites → 10px, 10 → 13px, 100 → 16px, 1000 → 20px, 10000 → 23px
    return Math.min(Math.max(10, 7 + Math.log10(citationCount + 1) * 4), 26);
  }

  /** Render the scatter chart */
  function renderChart(container, data) {
    const { papers, clusters } = data;

    // Group papers by cluster for separate traces (enables legend)
    const traces = clusters.map((cluster) => {
      const clusterPapers = papers.filter((p) => p.cluster === cluster.id);
      return {
        x: clusterPapers.map((p) => p.x),
        y: clusterPapers.map((p) => p.y),
        mode: "markers",
        type: "scatter",
        name: cluster.label,
        marker: {
          color: cluster.color,
          size: clusterPapers.map((p) => dotSize(p.citation_count)),
          opacity: 0.85,
          line: { width: 1, color: "#333" },
        },
        text: clusterPapers.map(
          (p) =>
            `<b>${p.title}</b><br>` +
            (p.authors.length
              ? `${p.authors.slice(0, 3).join(", ")}${p.authors.length > 3 ? " et al." : ""}<br>`
              : "") +
            (p.year ? `(${p.year})` : "") +
            (p.citation_count ? ` · ${p.citation_count.toLocaleString()} citations` : "")
        ),
        hoverinfo: "text",
        hoverlabel: {
          bgcolor: "#1a1a1a",
          font: { color: "#fff", size: 12 },
          bordercolor: cluster.color,
        },
        customdata: clusterPapers.map((p) => p.id),
      };
    });

    const isDark =
      document.body.classList.contains("dark") ||
      window.matchMedia("(prefers-color-scheme: dark)").matches;

    const layout = {
      title: {
        text: "Paper Clusters",
        font: { size: 14, color: isDark ? "#ddd" : "#333" },
      },
      showlegend: true,
      legend: {
        orientation: "h",
        y: -0.15,
        font: { size: 11, color: isDark ? "#ccc" : "#444" },
      },
      xaxis: {
        showgrid: false,
        zeroline: false,
        showticklabels: false,
        title: "",
      },
      yaxis: {
        showgrid: false,
        zeroline: false,
        showticklabels: false,
        title: "",
      },
      plot_bgcolor: isDark ? "#1e1e1e" : "#fafafa",
      paper_bgcolor: "transparent",
      margin: { t: 40, b: 50, l: 20, r: 20 },
      hovermode: "closest",
      height: 320,
    };

    const config = {
      displayModeBar: false,
      responsive: true,
    };

    window.Plotly.newPlot(container, traces, layout, config);

    // Click handler: scroll to paper in the list
    container.on("plotly_click", function (eventData) {
      if (!eventData.points || !eventData.points.length) return;
      const paperId = eventData.points[0].customdata;
      if (!paperId) return;

      // Find the paper card in the results list and scroll to it
      const card =
        document.querySelector(`[data-paper-id="${paperId}"]`) ||
        document.getElementById(`paper-${paperId}`);
      if (card) {
        card.scrollIntoView({ behavior: "smooth", block: "center" });
        card.style.outline = "2px solid #4e79a7";
        setTimeout(() => {
          card.style.outline = "";
        }, 2000);
      }
    });
  }

  /** Main entry: toggle cluster visualization */
  async function toggleClusterViz() {
    const container = document.getElementById("cluster-viz-container");
    const btn = document.getElementById("cluster-viz-btn");
    if (!container || !btn) return;

    // If already visible, hide it
    if (container.style.display !== "none" && container.innerHTML) {
      container.style.display = "none";
      btn.classList.remove("active");
      return;
    }

    // Prevent double-clicks
    if (isLoading) return;
    isLoading = true;
    btn.disabled = true;
    btn.textContent = " Loading…";

    try {
      await ensurePlotly();
      const data = await fetchClusters();

      container.innerHTML = "";
      container.style.display = "block";
      renderChart(container, data);
      btn.classList.add("active");
    } catch (err) {
      container.style.display = "block";
      const errDiv = document.createElement("div");
      errDiv.style.cssText = "padding:8px;color:#e15759;font-size:0.85rem;";
      errDiv.textContent = "Clustering failed: " + (err.message || "Unknown error");
      container.innerHTML = "";
      container.appendChild(errDiv);
    } finally {
      isLoading = false;
      btn.disabled = false;
      btn.innerHTML =
        '<i class="fa-solid fa-diagram-project"></i> Visualize';
    }
  }

  // Bind click handler via event delegation (works with HTMX-loaded content)
  document.addEventListener("click", function (e) {
    const btn = e.target.closest("#cluster-viz-btn");
    if (btn) {
      e.preventDefault();
      toggleClusterViz();
    }
  });
})();
