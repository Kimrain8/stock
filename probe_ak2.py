# import seed_browser_use as bu
# import time, json
# bu.navigate("https://webapi.cninfo.com.cn/#/dataBrowse")
# bu.wait_for_load(timeout=25)
# time.sleep(3)
# out = bu.js("""
# (async () => {
#   const enc = window.indexcode.getResCode();
#   const r = await fetch('/api-cloud-gateway-manage/apiDoc/info?gatewayCode=66ac85d341c4484a9d287f31ba59cc80', {headers:{'Accept-EncKey':enc,'X-Requested-With':'XMLHttpRequest'}});
#   const j = await r.json();
#   const rc = (j.data && j.data.resultContent) || {};
#   const bi = rc.baseInfo || {};
#   const fields = JSON.parse(rc.outputParameter || '[]');
#   const rows = fields.map(f => (f.fieldName||f.name||'') + '|' + (f.alias||f.fieldChineseName||'') + '|' + (f.dataType||''));
#   const inp = (j.data && j.data.requestConfig && j.data.requestConfig.inputParameter) || [];
#   const inrows = inp.map(f => (f.fieldName||f.name||'') + '|' + (f.alias||''));
#   return JSON.stringify({name: bi.name, alias: bi.alias, describe: bi.describe, inparams: inrows, fields: rows});
# })()
# """)
# print(out)

import time
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
import base64
def get_enckey( force: bool = False) -> str:
    now = time.time()
    # if (not force and self._ENKEY_CACHE["value"] and now - (self._ENKEY_CACHE["time"] or 0) < 5400):
    #     return self._ENKEY_CACHE["value"]
    key = iv = b"1234567887654321"
    plain = str(int(now)).encode()
    cipher = AES.new(key, AES.MODE_CBC, iv)
    token = base64.b64encode(cipher.encrypt(pad(plain, 16))).decode()
    # self._ENKEY_CACHE["value"], self._ENKEY_CACHE["time"] = token, now
    return token

if __name__ == "__main__":
    print(get_enckey())