# cython: language_level=3
# _jdl.pyx — Cython binding to the jdl-hotpath Rust engine (C ABI / cdylib).
#
# This is the fast native path: it calls straight into libjdl_hotpath with no
# subprocess. Build it with setup.py (needs the Rust cdylib built first:
#   cd rust/hotpath && cargo build --release).
import json

cdef extern from "jdl_hotpath.h":
    char* jdl_scan(const char* input)
    char* jdl_analyze(const char* input)
    void  jdl_string_free(char* ptr)


cdef _call(char* (*fn)(const char*), bytes payload):
    cdef char* out = fn(payload)
    if out is NULL:
        return {"error": "native returned NULL"}
    try:
        return json.loads(out.decode("utf-8"))
    finally:
        jdl_string_free(out)


def scan(dict request):
    """Run the arbitrage hot-path. request is a ScanRequest dict; returns ScanResult."""
    return _call(jdl_scan, json.dumps(request).encode("utf-8"))


def analyze(str bytecode):
    """Analyze contract bytecode (hex). Returns an AnalysisReport dict."""
    return _call(jdl_analyze, json.dumps({"bytecode": bytecode}).encode("utf-8"))


def backend():
    return "cython"
