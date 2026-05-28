(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;

  const { React } = SDK;
  const { useEffect, useMemo, useState } = SDK.hooks;
  const h = React.createElement;

  const API = "/api/plugins/hermes-lcm";

  function short(s, n) {
    const text = String(s || "");
    return text.length > n ? text.slice(0, n - 1) + "…" : text;
  }

  function BarList(props) {
    const rows = props.rows || [];
    const keyName = props.keyName;
    const total = rows.reduce((acc, row) => acc + (Number(row.count) || 0), 0) || 1;
    if (!rows.length) return h("div", { className: "hermes-lcm-empty" }, "No data");
    return h("div", { className: "hermes-lcm-bars" }, rows.map(function (row, idx) {
      const label = String(row[keyName] == null ? "(none)" : row[keyName]);
      const count = Number(row.count) || 0;
      const pct = Math.max(2, Math.round((count / total) * 100));
      return h("div", { key: label + ":" + idx, className: "hermes-lcm-bar-row" }, [
        h("div", { className: "hermes-lcm-bar-head" }, [
          h("span", { className: "hermes-lcm-k" }, label),
          h("span", { className: "hermes-lcm-v" }, String(count)),
        ]),
        h("div", { className: "hermes-lcm-bar-track" }, [
          h("div", { className: "hermes-lcm-bar-fill", style: { width: pct + "%" } }),
        ]),
      ]);
    }));
  }

  function App() {
    const [q, setQ] = useState("");
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");

    const debouncedQ = useMemo(function () { return q.trim(); }, [q]);

    useEffect(function () {
      let active = true;
      setLoading(true);
      setError("");
      const url = debouncedQ ? `${API}/overview?q=${encodeURIComponent(debouncedQ)}&limit=25` : `${API}/overview?limit=25`;
      SDK.fetchJSON(url).then(function (json) {
        if (active) setData(json);
      }).catch(function (err) {
        if (active) setError(String((err && err.message) || err));
      }).finally(function () {
        if (active) setLoading(false);
      });
      return function () { active = false; };
    }, [debouncedQ]);

    const overview = (data && data.overview) || {};
    const matches = (data && data.matches) || { messages: [], summary_nodes: [] };

    return h("div", { className: "hermes-lcm" }, [
      h("div", { className: "hermes-lcm-top" }, [
        h("input", {
          className: "hermes-lcm-search",
          value: q,
          placeholder: "Search messages and summaries",
          onChange: function (e) { setQ(e.target.value || ""); },
        }),
        h("div", { className: "hermes-lcm-status" },
          loading ? "Loading…" : ((data && data.exists) ? "Database detected" : "Database missing")
        ),
      ]),
      h("div", { className: "hermes-lcm-path" }, data ? data.path : ""),
      error ? h("div", { className: "hermes-lcm-error" }, error) : null,
      data && data.error ? h("div", { className: "hermes-lcm-error" }, data.error) : null,
      h("div", { className: "hermes-lcm-grid" }, [
        h("div", { className: "hermes-lcm-card" }, [
          h("h3", null, "Counts"),
          h("ul", { className: "hermes-lcm-list" }, [
            h("li", null, `Messages: ${overview.messages_total || 0}`),
            h("li", null, `Sessions: ${overview.sessions_total || 0}`),
            h("li", null, `Summary nodes: ${overview.summary_nodes_total || 0}`),
            h("li", null, `Summary sessions: ${overview.summary_node_sessions_total || 0}`),
            h("li", null, `Max depth: ${overview.max_summary_depth || 0}`),
          ]),
        ]),
        h("div", { className: "hermes-lcm-card" }, [
          h("h3", null, "By Source"),
          h(BarList, { rows: overview.source_counts || [], keyName: "source" }),
        ]),
        h("div", { className: "hermes-lcm-card" }, [
          h("h3", null, "By Role"),
          h(BarList, { rows: overview.role_counts || [], keyName: "role" }),
        ]),
        h("div", { className: "hermes-lcm-card" }, [
          h("h3", null, "Summary Depth"),
          h(BarList, { rows: overview.depth_counts || [], keyName: "depth" }),
        ]),
      ]),
      h("div", { className: "hermes-lcm-grid" }, [
        h("div", { className: "hermes-lcm-card" }, [
          h("h3", null, "Recent Sessions"),
          h("ul", { className: "hermes-lcm-list" },
            ((data && data.latest_sessions) || []).map(function (s, idx) {
              return h("li", { key: s.session_id + ":" + idx }, `${short(s.session_id, 48)} (${s.message_count})`);
            })
          ),
        ]),
        h("div", { className: "hermes-lcm-card" }, [
          h("h3", null, "Latest Summaries"),
          h("ul", { className: "hermes-lcm-list" },
            ((data && data.latest_summary_nodes) || []).map(function (n) {
              return h("li", { key: n.node_id }, `D${n.depth} ${short(n.session_id, 32)}: ${short(n.summary, 120)}`);
            })
          ),
        ]),
      ]),
      debouncedQ ? h("div", { className: "hermes-lcm-grid" }, [
        h("div", { className: "hermes-lcm-card" }, [
          h("h3", null, `Matching Messages (${(matches.messages || []).length})`),
          h("ul", { className: "hermes-lcm-list" }, (matches.messages || []).map(function (m) {
            return h("li", { key: m.store_id }, `${m.role}/${m.source} ${short(m.session_id, 28)}: ${short(m.content, 140)}`);
          })),
        ]),
        h("div", { className: "hermes-lcm-card" }, [
          h("h3", null, `Matching Summaries (${(matches.summary_nodes || []).length})`),
          h("ul", { className: "hermes-lcm-list" }, (matches.summary_nodes || []).map(function (n) {
            return h("li", { key: n.node_id }, `D${n.depth} ${short(n.session_id, 28)}: ${short(n.summary, 140)}`);
          })),
        ]),
      ]) : null,
    ]);
  }

  if (window.__HERMES_PLUGINS__ && typeof window.__HERMES_PLUGINS__.register === "function") {
    window.__HERMES_PLUGINS__.register("hermes-lcm", App);
  }
})();
