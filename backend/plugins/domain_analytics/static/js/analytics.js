/**
 * Domain Analytics — Chart.js Visualizer & Dynamic Controller
 */
document.addEventListener('DOMContentLoaded', () => {
  initTrafficChart();
  initStatusChart();
});

function initTrafficChart() {
  const ctx = document.getElementById('trafficChart');
  if (!ctx || typeof Chart === 'undefined') return;

  const labels = TIMELINE_DATA.map(d => d.hour_timestamp.slice(5, 16)); // MM-DD HH:00
  const requests = TIMELINE_DATA.map(d => d.total_requests);
  const visitors = TIMELINE_DATA.map(d => d.unique_ips);

  new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels.length ? labels : ['No Data'],
      datasets: [
        {
          label: 'Requests',
          data: requests.length ? requests : [0],
          borderColor: '#3B82F6',
          backgroundColor: 'rgba(59, 130, 246, 0.08)',
          fill: true,
          tension: 0.35,
          borderWidth: 2,
          pointRadius: requests.length > 30 ? 0 : 3,
        },
        {
          label: 'Visitors',
          data: visitors.length ? visitors : [0],
          borderColor: '#8B5CF6',
          backgroundColor: 'rgba(139, 92, 246, 0.05)',
          fill: true,
          tension: 0.35,
          borderWidth: 2,
          pointRadius: visitors.length > 30 ? 0 : 3,
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          position: 'top',
          labels: { color: '#A1A1AA', boxWidth: 12, font: { size: 12 } }
        },
        tooltip: {
          backgroundColor: '#18181B',
          titleColor: '#FAFAFA',
          bodyColor: '#D4D4D8',
          borderColor: 'rgba(255, 255, 255, 0.1)',
          borderWidth: 1,
          padding: 10,
        }
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { color: '#71717A', maxRotation: 0, autoSkip: true, maxTicksLimit: 8 }
        },
        y: {
          beginAtZero: true,
          grid: { color: 'rgba(255, 255, 255, 0.04)' },
          ticks: { color: '#71717A', precision: 0 }
        }
      }
    }
  });
}

function initStatusChart() {
  const ctx = document.getElementById('statusChart');
  if (!ctx || typeof Chart === 'undefined') return;

  const dataValues = [
    STATUS_TOTALS['2xx'] || 0,
    STATUS_TOTALS['3xx'] || 0,
    STATUS_TOTALS['4xx'] || 0,
    STATUS_TOTALS['5xx'] || 0
  ];

  const hasData = dataValues.some(v => v > 0);

  new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: ['2xx OK', '3xx Redirect', '4xx Client Error', '5xx Server Crash'],
      datasets: [{
        data: hasData ? dataValues : [1],
        backgroundColor: hasData ? ['#10B981', '#06B6D4', '#F59E0B', '#EF4444'] : ['rgba(255, 255, 255, 0.08)'],
        borderWidth: 0,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '70%',
      plugins: {
        legend: {
          position: 'bottom',
          labels: { color: '#A1A1AA', boxWidth: 10, font: { size: 11 } }
        },
        tooltip: {
          enabled: hasData,
          backgroundColor: '#18181B',
          borderColor: 'rgba(255, 255, 255, 0.1)',
          borderWidth: 1,
        }
      }
    }
  });
}

async function toggleCurrentDomain(isActive) {
  try {
    const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
    const res = await fetch(`/plugins/domain_analytics/api/domain/${encodeURIComponent(DOMAIN_NAME)}/toggle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify({ is_active: isActive })
    });
    if (!res.ok) throw new Error('Failed to update status');
    window.location.reload();
  } catch (err) {
    alert('Error updating tracking status: ' + err.message);
  }
}
