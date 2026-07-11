# Power Meter Demo

服务地址：

```text
http://ow.lixinrui000.cn:18988
```

下面这个 Python demo 会：

1. 使用连接到 Pixel 9 的功率计 `FNB-58-53204`
2. 测量一段 task 的耗电量：  create session-> start -> 测量一段时间 -> stop -> read_nrg
3. 打印 task 能耗，单位是毫瓦时 `mWh`

```python
import time
import requests


BASE_URL = "http://ow.lixinrui000.cn:18988"
DEVICE_NAME = "FNB-58-53204"  # Power meter connected to Pixel 9


def post_control(device_name, command):
    response = requests.post(
        f"{BASE_URL}/energy_control",
        json={
            "device_name": device_name,
            "energy_ctrl_msg": command,
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def main():
    requests.get(f"{BASE_URL}/handshake", timeout=10).raise_for_status()
    print("Using power meter:", DEVICE_NAME)

    post_control(DEVICE_NAME, "create")
    post_control(DEVICE_NAME, "start")

    # Put the measured workload here.
    time.sleep(5)

    post_control(DEVICE_NAME, "stop")
    result = post_control(DEVICE_NAME, "read_nrg")
    # The server returns Wh. Convert Wh to mWh.
    energy_mwh = result["energy_wh"] * 1000

    print("task energy =", energy_mwh, "mWh")
    print("raw response =", result)


if __name__ == "__main__":
    main()
```

运行：

```powershell
python demo_power_meter.py
```
