#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日早安 / 天气 + 恋爱小情书 推送到 微信模板消息。

匹配模板 template_id 的字段（共 8 个）：
  date, city, weather, min_temperature, max_temperature,
  love_day, birthday2, pipi

依赖：仅 Python 标准库（urllib / gzip / json）。
天气：和风天气 QWeather REST API
文案：天行数据 TianAPI（彩虹屁 caihongpi，失败自动兜底）
推送：微信公众号模板消息接口

Secrets（GitHub Actions）：
  APP_ID / APP_SECRET / USER_ID / TEMPLATE_ID  微信公众平台
  CITY           城市中文名，如 福州
  HEFENG_KEY     和风天气 API key
  TIAN_KEY       天行数据 API key
  START_DATE         恋爱开始日期 YYYY-MM-DD  -> love_day
  JINGJING_BIRTHDAY  婧婧生日 MM-DD            -> birthday2
  PIPI_TEXT  自定义彩虹屁文案（可选，覆盖天行 API）
本地调试同目录放 config.json 亦可（结构同上）。
"""
import json
import os
import sys
import gzip
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, date


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def _decode(resp):
    raw = resp.read()
    if resp.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def http_get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={
        "User-Agent": "daily-push/1.0", "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return _decode(r)


def http_post_json(url, payload, timeout=15):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json; charset=utf-8",
        "Accept-Encoding": "gzip", "User-Agent": "daily-push/1.0"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return _decode(r)


# ---------------- 和风天气 ----------------
HEFENG_GEO = "https://geoapi.qweather.com/v2/city/lookup"
HEFENG_3D = "https://devapi.qweather.com/v7/weather/3d"
HEFENG_NOW = "https://devapi.qweather.com/v7/weather/now"


def hefeng_lookup(key, city):
    q = urllib.parse.urlencode({"location": city, "key": key, "range": "cn"})
    d = http_get_json(f"{HEFENG_GEO}?{q}")
    if d.get("code") != "200" or not d.get("location"):
        raise RuntimeError(f"城市查询失败: {city} (code={d.get('code')})")
    loc = d["location"][0]
    adm1 = loc.get("adm1", "").replace("省", "").replace("市", "")
    name = loc["name"]
    full = f"{adm1} {name}" if adm1 and adm1 != name else name
    return loc["id"], full


def hefeng_weather(key, lid):
    q = urllib.parse.urlencode({"location": lid, "key": key})
    d3 = http_get_json(f"{HEFENG_3D}?{q}")
    if d3.get("code") != "200":
        raise RuntimeError(f"天气预报查询失败: code={d3.get('code')}")
    today = d3["daily"][0]
    now = http_get_json(f"{HEFENG_NOW}?{q}")
    now_data = now["now"] if now.get("code") == "200" else None
    return today, now_data


# ---------------- 天行数据 TianAPI ----------------
TIAN_BASE = "https://apis.tianapi.com"


def tianapi_text(name, key):
    """调用天行接口，抽取一句文本；失败/未申请返回 None（由调用方兜底）。"""
    if not key:
        return None
    url = f"{TIAN_BASE}/{name}/index?key={urllib.parse.quote(key)}"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "daily-push/1.0", "Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            d = json.loads(raw.decode("utf-8"))
    except Exception as e:
        log(f"天行接口 {name} 请求异常: {e}")
        return None
    if d.get("code") != 200:
        log(f"天行接口 {name} 返回 code={d.get('code')} msg={d.get('msg')}")
        return None
    res = d.get("result")
    if isinstance(res, dict):
        for k in ("content", "word", "saying", "en", "zh"):
            if res.get(k):
                return str(res[k])
    if isinstance(res, list) and res:
        item = res[0]
        if isinstance(item, dict):
            for k in ("content", "word", "saying"):
                if item.get(k):
                    return str(item[k])
        return str(item)
    return None


# ---------------- 微信 ----------------
WX_TOKEN = "https://api.weixin.qq.com/cgi-bin/token"
WX_SEND = "https://api.weixin.qq.com/cgi-bin/message/template/send"


def wx_token(appid, secret):
    q = urllib.parse.urlencode({
        "grant_type": "client_credential", "appid": appid, "secret": secret})
    d = http_get_json(f"{WX_TOKEN}?{q}")
    if "access_token" not in d:
        raise RuntimeError(f"微信获取token失败: {d}")
    return d["access_token"]


def wx_send(token, payload):
    d = http_post_json(f"{WX_SEND}?access_token={token}", payload)
    if d.get("errcode") != 0:
        raise RuntimeError(f"微信发送失败 errcode={d.get('errcode')} errmsg={d.get('errmsg')}")
    return d


# ---------------- 计算 ----------------
def days_since(date_str):
    start = datetime.strptime(date_str, "%Y-%m-%d").date()
    return (date.today() - start).days + 1


def days_until(mmdd):
    m, d = map(int, mmdd.split("-"))
    today = date.today()
    cand = date(today.year, m, d)
    if cand < today:
        cand = date(today.year + 1, m, d)
    return (cand - today).days


def tips_for(today):
    text = today.get("textDay", "")
    pop = today.get("pop", "")
    try:
        pop_i = int(pop)
    except (ValueError, TypeError):
        pop_i = 0
    if pop_i >= 50:
        return "降雨概率较高，记得带伞 ☔"
    if "雨" in text:
        return "今天可能有雨，出门带伞"
    if "雪" in text:
        return "有雪，注意保暖防滑"
    if "晴" in text:
        return "天气晴好，适合出门走走"
    return "天气尚可，注意补水"


def env(key, default=""):
    return os.environ.get(key) or default


def build_payload():
    appid = env("APP_ID")
    secret = env("APP_SECRET")
    user = env("USER_ID")
    tpl = env("TEMPLATE_ID")
    hkey = env("HEFENG_KEY")
    tkey = env("TIAN_KEY")
    city = env("CITY", "福州")
    start = env("START_DATE")
    jingjing = env("JINGJING_BIRTHDAY")

    if not all([appid, secret, user, tpl]):
        raise RuntimeError("缺少微信配置 APP_ID/APP_SECRET/USER_ID/TEMPLATE_ID")
    if not hkey:
        raise RuntimeError("缺少 HEFENG_KEY（天气 key）")

    # 个人必填字段（缺失则无法计算，跳过发送）
    missing = [k for k, v in (("START_DATE", start), ("JINGJING_BIRTHDAY", jingjing)) if not v]
    if missing:
        return None, missing

    lid, city_full = hefeng_lookup(hkey, city)
    log(f"城市: {city_full}")
    today, now_data = hefeng_weather(hkey, lid)
    log("天气获取完成")

    love = str(days_since(start))
    b2 = str(days_until(jingjing))

    now = datetime.now()
    week = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][now.weekday()]
    date_str = f"{now.year}年{now.month}月{now.day}日 {week}"

    weather_text = today.get("textDay", "")
    if now_data and now_data.get("text"):
        weather_text = now_data["text"]

    # 文案：pipi 走天行彩虹屁；失败用兜底
    pipi = env("PIPI_TEXT") or tianapi_text("caihongpi", tkey) or "你笑起来的样子最好看。"

    data = {
        "date": {"value": date_str},
        "city": {"value": city_full},
        "weather": {"value": weather_text},
        "min_temperature": {"value": today.get("tempMin", "")},
        "max_temperature": {"value": today.get("tempMax", "")},
        "love_day": {"value": love},
        "birthday2": {"value": b2},
        "pipi": {"value": pipi},
    }
    payload = {"touser": user, "template_id": tpl, "data": data}
    return payload, missing


def main():
    payload, missing = build_payload()
    if payload is None:
        log("⚠️ 个人字段未配置，跳过发送: " + ", ".join(missing))
        return

    dry = env("DRY_RUN") in ("1", "true", "True")
    if dry:
        log("DRY_RUN 模式：仅打印，不发送")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    token = wx_token(env("APP_ID"), env("APP_SECRET"))
    log("获取 access_token 成功")
    resp = wx_send(token, payload)
    log(f"✅ 推送成功: {resp}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"❌ 失败: {e}")
        sys.exit(1)
