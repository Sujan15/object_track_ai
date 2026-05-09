(function() {
    let classPieChart, hourlyBarChart;

    async function loadClassDistribution(days = 7) {
        try {
            const res = await fetch(`/api/analytics/class-distribution?days=${days}`);
            const data = await res.json();
            const labels = Object.keys(data.class_distribution);
            const counts = Object.values(data.class_distribution);
            if (classPieChart) {
                classPieChart.data.labels = labels;
                classPieChart.data.datasets[0].data = counts;
                classPieChart.update();
            } else {
                const ctx = document.getElementById('classPieChart').getContext('2d');
                classPieChart = new Chart(ctx, {
                    type: 'pie',
                    data: { labels, datasets: [{ data: counts, backgroundColor: ['#4a6fa5','#ff9800','#4caf50','#f44336','#9c27b0'] }] }
                });
            }
        } catch(e) { console.error(e); }
    }

    async function loadHourly(days = 7) {
        try {
            const res = await fetch(`/api/analytics/hourly?days=${days}`);
            const data = await res.json();
            const labels = data.map(d => d.hour);
            const counts = data.map(d => d.count);
            if (hourlyBarChart) {
                hourlyBarChart.data.labels = labels;
                hourlyBarChart.data.datasets[0].data = counts;
                hourlyBarChart.update();
            } else {
                const ctx = document.getElementById('hourlyBarChart').getContext('2d');
                hourlyBarChart = new Chart(ctx, {
                    type: 'bar',
                    data: { labels, datasets: [{ label: 'Objects per hour', data: counts, backgroundColor: '#ff9800' }] }
                });
            }
        } catch(e) { console.error(e); }
    }

    async function exportExcel() {
        // Minimal CSV export for demo
        let csv = "Timestamp,Line,Class,Defective\n";
        // In a real implementation you'd fetch detailed logs from an endpoint
        const blob = new Blob([csv], {type: 'text/csv'});
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `analytics_${new Date().toISOString().slice(0,19)}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    }

    async function exportPDF() {
        alert("PDF export will be implemented in the next version.");
    }

    document.addEventListener('DOMContentLoaded', () => {
        loadClassDistribution(7);
        loadHourly(7);
        document.querySelectorAll('.period-tab').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.period-tab').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                const days = parseInt(btn.dataset.days);
                loadClassDistribution(days);
                loadHourly(days);
            });
        });
        document.getElementById('exportExcel')?.addEventListener('click', exportExcel);
        document.getElementById('exportPDF')?.addEventListener('click', exportPDF);
    });
})();