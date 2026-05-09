(function() {
    let classChart, hourlyChart;

    function updateUI(stats) {
        document.getElementById('totalObjects').innerText = stats.totalObjects.toLocaleString();
        document.getElementById('totalDefects').innerText = stats.totalDefects.toLocaleString();
        const activeLines = Object.keys(stats.allLines || {}).length;
        document.getElementById('activeLines').innerText = activeLines;

        const classLabels = Object.keys(stats.classTotals);
        const classCounts = Object.values(stats.classTotals);
        if (classChart) {
            classChart.data.labels = classLabels;
            classChart.data.datasets[0].data = classCounts;
            classChart.update();
        } else {
            const ctx = document.getElementById('classChart').getContext('2d');
            classChart = new Chart(ctx, {
                type: 'bar',
                data: { labels: classLabels, datasets: [{ label: 'Count', data: classCounts, backgroundColor: '#4a6fa5' }] },
                options: { responsive: true, maintainAspectRatio: true }
            });
        }

        const tbody = document.getElementById('lineTableBody');
        if (tbody) {
            const rows = [];
            for (const [lineId, data] of Object.entries(stats.lineTotals)) {
                const lineStats = stats.allLines[lineId]?.stats || {};
                const total = data.total;
                const defects = data.defects;
                const rate = total > 0 ? ((defects / total) * 100).toFixed(1) : '0';
                let topClass = 'N/A';
                if (lineStats.classes) {
                    const entries = Object.entries(lineStats.classes);
                    if (entries.length) topClass = entries.reduce((a,b) => a[1] > b[1] ? a : b)[0];
                }
                rows.push(`<td><td>Line ${lineId}</td><td>${total}</td><td>${defects}</td><td>${rate}%</td><td>${topClass}</td></tr>`);
            }
            tbody.innerHTML = rows.length ? rows.join('') : '<tr><td colspan="5">No data</td></tr>';
        }
    }

    async function loadHourlyTrend(hours = 24) {
        try {
            const res = await fetch(`/api/analytics/hourly?days=${Math.ceil(hours/24)}`);
            const data = await res.json();
            const labels = data.map(d => d.hour);
            const counts = data.map(d => d.count);
            if (hourlyChart) {
                hourlyChart.data.labels = labels;
                hourlyChart.data.datasets[0].data = counts;
                hourlyChart.update();
            } else {
                const ctx = document.getElementById('hourlyChart').getContext('2d');
                hourlyChart = new Chart(ctx, {
                    type: 'line',
                    data: { labels, datasets: [{ label: 'Objects per hour', data: counts, borderColor: '#ff9800', fill: false }] },
                    options: { responsive: true }
                });
            }
        } catch(e) { console.error('Hourly trend error', e); }
    }

    function exportReport() {
        let csv = "Line,Total,Defects,Defect Rate,Top Class\n";
        const rows = document.querySelectorAll('#lineTableBody tr');
        rows.forEach(row => {
            const cells = row.querySelectorAll('td');
            if (cells.length === 5) {
                csv += `${cells[0].innerText},${cells[1].innerText},${cells[2].innerText},${cells[3].innerText},${cells[4].innerText}\n`;
            }
        });
        const blob = new Blob([csv], {type: 'text/csv'});
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `report_${new Date().toISOString().slice(0,19)}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    }

    document.addEventListener('DOMContentLoaded', () => {
        window.App.registerStatsHandler(updateUI);
        const rangeSelect = document.getElementById('hourlyRange');
        if (rangeSelect) {
            rangeSelect.addEventListener('change', () => loadHourlyTrend(parseInt(rangeSelect.value)));
            loadHourlyTrend(24);
        } else {
            loadHourlyTrend(24);
        }
        document.getElementById('exportReportBtn')?.addEventListener('click', exportReport);
    });
})();