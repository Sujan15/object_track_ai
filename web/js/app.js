(function() {
    const lineIds = [1, 2, 3, 4]; // adjust based on your cameras.yaml
    const state = { lineIds, lastStats: {} };
    const statsHandlers = new Set();

    function registerStatsHandler(handler) {
        statsHandlers.add(handler);
        return () => statsHandlers.delete(handler);
    }

    function startStatsSocket() {
        const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
        const socket = new WebSocket(`${protocol}://${location.host}/ws/stats`);
        socket.onmessage = (event) => {
            let allLines;
            try { allLines = JSON.parse(event.data); } catch(e) { return; }
            let totalObjects = 0, totalDefects = 0;
            const classTotals = {};
            const lineTotals = {};

            for (const [id, data] of Object.entries(allLines || {})) {
                if (!data || !data.stats) continue;
                const stats = data.stats;
                const lineTotal = stats.total || 0;
                const defects = stats.defects || 0;
                lineTotals[id] = { total: lineTotal, defects };
                totalObjects += lineTotal;
                totalDefects += defects;
                for (const [cls, cnt] of Object.entries(stats.classes || {})) {
                    classTotals[cls] = (classTotals[cls] || 0) + cnt;
                }
                state.lastStats[id] = { total: lineTotal, broken: defects };
            }

            statsHandlers.forEach(handler => {
                try { handler({ allLines, totalObjects, totalDefects, classTotals, lineTotals }); } catch(e) {}
            });
        };
        socket.onerror = () => {};
    }

    window.App = { registerStatsHandler, state };
    startStatsSocket();
})();