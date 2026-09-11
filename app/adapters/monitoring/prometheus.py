import logging
import time

import psutil
from prometheus_client import Counter, Histogram, Gauge, generate_latest

# 定义指标
REQUEST_COUNT = Counter('aida_requests_total', 'Total requests', ['method', 'endpoint', 'status'])
REQUEST_DURATION = Histogram('aida_request_duration_seconds', 'Request duration', ['method', 'endpoint'])
ACTIVE_CONNECTIONS = Gauge('aida_active_connections', 'Active database connections')
SYSTEM_CPU_USAGE = Gauge('aida_system_cpu_usage_percent', 'System CPU usage')
SYSTEM_MEMORY_USAGE = Gauge('aida_system_memory_usage_percent', 'System memory usage')

try:
    import GPUtil
    gpu_available = True
    GPU_UTILIZATION = Gauge('aida_gpu_utilization_percent', 'GPU utilization', ['gpu_id'])
    GPU_MEMORY_USAGE = Gauge('aida_gpu_memory_usage_percent', 'GPU memory usage', ['gpu_id'])
except ImportError:
    gpu_available = False

logger = logging.getLogger(__name__)

class PrometheusMetrics:
    """Prometheus指标管理"""
    
    def __init__(self):
        self.start_time = time.time()
    
    def record_request(self, method: str, endpoint: str, status_code: int, duration: float):
        """记录请求指标"""
        REQUEST_COUNT.labels(method=method, endpoint=endpoint, status=str(status_code)).inc()
        REQUEST_DURATION.labels(method=method, endpoint=endpoint).observe(duration)

    def update_system_metrics(self):
        """更新系统指标"""
        try:
            # CPU和内存使用率
            cpu_percent = psutil.cpu_percent(interval=1)
            memory_percent = psutil.virtual_memory().percent
            
            SYSTEM_CPU_USAGE.set(cpu_percent)
            SYSTEM_MEMORY_USAGE.set(memory_percent)
            
            # GPU指标（如果可用）
            if gpu_available:
                try:
                    gpus = GPUtil.getGPUs()
                    for i, gpu in enumerate(gpus):
                        GPU_UTILIZATION.labels(gpu_id=str(i)).set(gpu.load * 100)
                        GPU_MEMORY_USAGE.labels(gpu_id=str(i)).set(gpu.memoryUtil * 100)
                except Exception as e:
                    logger.warning(f"Failed to collect GPU metrics: {e}")
            
        except Exception as e:
            logger.error(f"Failed to update system metrics: {e}")
    
    def get_metrics(self) -> str:
        """获取Prometheus格式的指标"""
        self.update_system_metrics()
        return generate_latest()

# 全局指标实例
metrics = PrometheusMetrics() 