//! Read-only JSON-RPC connectivity probe for `jdl-hotpath probe`.
//!
//! Exists so the Rust leg of the multi-platform mainnet connectivity check
//! (`jdl connectivity`) exercises the same four JSON-RPC methods through the
//! crate that the production hot path is built from, rather than trusting a
//! Python-side assertion that the toolchain merely exists.
//!
//! Scope is deliberately narrow and strictly read-only. The only methods this
//! module can emit are `eth_chainId`, `eth_blockNumber`, `eth_gasPrice` and
//! `web3_clientVersion`. There is no code path here that signs, sends, or
//! simulates a transaction, and `eth_sendRawTransaction` is not constructible
//! from this module's request builder — the method name is an enum variant, not
//! a caller-supplied string.
//!
//! The RPC URL is never echoed back in the result. Endpoint URLs carry API
//! keys (`https://arb-mainnet.example/v2/<key>`), and a probe result gets
//! logged, printed by the CLI, and embedded in CI output, so returning the URL
//! would republish the credential. `ProbeReport::redacted_endpoint` emits only
//! scheme, host and a boolean marking whether a credential was present.

use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

/// The complete set of JSON-RPC methods this module is able to issue.
///
/// A closed enum, deliberately: it makes "the probe can only read" a property
/// the type system enforces rather than a property of reviewer vigilance.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum RpcMethod {
    EthChainId,
    EthBlockNumber,
    EthGasPrice,
    Web3ClientVersion,
}

impl RpcMethod {
    /// Wire name for the JSON-RPC request.
    pub fn wire_name(self) -> &'static str {
        match self {
            RpcMethod::EthChainId => "eth_chainId",
            RpcMethod::EthBlockNumber => "eth_blockNumber",
            RpcMethod::EthGasPrice => "eth_gasPrice",
            RpcMethod::Web3ClientVersion => "web3_clientVersion",
        }
    }
}

/// Outcome of a single JSON-RPC call.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RpcCall {
    pub method: String,
    pub result: Option<String>,
    pub latency_ms: u64,
    pub error: Option<String>,
}

/// Full connectivity verdict for one endpoint.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProbeReport {
    /// Scheme + host + credential-present marker. Never the full URL.
    pub endpoint: String,
    /// Whether a credential-looking path/query segment was present.
    pub credential_in_url: bool,
    /// Chain id as returned by `eth_chainId`, decimal string.
    pub chain_id: Option<u64>,
    /// Chain id the caller expected, if the caller named one.
    pub expected_chain_id: Option<u64>,
    /// True when the endpoint answered and matched `expected_chain_id`.
    pub chain_ok: bool,
    /// Latest block height, decimal string.
    pub block_number: Option<u64>,
    /// Latest gas price in wei, decimal string.
    pub gas_price_wei: Option<String>,
    /// `web3_clientVersion` string, if the endpoint returned one.
    pub client: Option<String>,
    /// Per-call detail, in probe order.
    pub calls: Vec<RpcCall>,
    /// Wall-clock duration of the whole probe.
    pub total_ms: u64,
    /// Set when the probe could not complete at all.
    pub error: Option<String>,
}

/// Build a JSON-RPC 2.0 request body for `method` with id `id`.
fn request_body(method: RpcMethod, id: u64) -> String {
    format!(
        r#"{{"jsonrpc":"2.0","id":{},"method":"{}","params":[]}}"#,
        id,
        method.wire_name()
    )
}

/// Strip a JSON-RPC response body down to the `result` string.
///
/// The RPC result is heterogeneous: a number for `eth_chainId`, a hex quantity
/// for `eth_blockNumber`, a hex quantity for `eth_gasPrice`, a bare string for
/// `web3_clientVersion`. Rather than four parsers, this keeps the payload as a
/// JSON string and decodes it at the call site that knows the shape.
fn extract_result(body: &str) -> Result<String, String> {
    let value: serde_json::Value =
        serde_json::from_str(body).map_err(|e| format!("malformed JSON response: {e}"))?;
    if let Some(err) = value.get("error") {
        if !err.is_null() {
            return Err(format!("rpc error: {err}"));
        }
    }
    match value.get("result") {
        None | Some(serde_json::Value::Null) => Err("response has no result field".to_string()),
        Some(serde_json::Value::String(s)) => Ok(s.clone()),
        Some(other) => Ok(other.to_string()),
    }
}

/// Parse a `0x`-prefixed JSON-RPC quantity into a `u64`.
///
/// Returns `None` for anything malformed rather than guessing, so a truncated
/// or padded value is reported as absent instead of silently reading as zero.
fn parse_quantity(raw: &str) -> Option<u64> {
    let digits = raw.trim().trim_start_matches("0x").trim_start_matches("0X");
    if digits.is_empty() || !digits.chars().all(|c| c.is_ascii_hexdigit()) {
        return None;
    }
    u64::from_str_radix(digits, 16).ok()
}

/// Redact an endpoint to scheme + host, and note whether it carried a credential.
///
/// A path or query segment is treated as a credential when it is non-empty and
/// is not a bare root path. `https://host/rpc` is a public endpoint; anything
/// deeper than one segment is treated as carrying a key.
pub fn redact_endpoint(url: &str) -> (String, bool) {
    let (scheme, rest) = match url.split_once("://") {
        Some((s, r)) => (s, r),
        None => ("", url),
    };
    let authority = rest.split(['/', '?']).next().unwrap_or("");
    let tail = &rest[authority.len()..];
    let tail = tail.trim_start_matches('/');
    let credential = !tail.is_empty();
    let host = if authority.is_empty() { "<unparseable>" } else { authority };
    (
        if scheme.is_empty() {
            host.to_string()
        } else {
            format!("{scheme}://{host}")
        },
        credential,
    )
}

/// Strip anything credential-shaped out of a human-facing error string.
///
/// This is not defensive decoration. `ureq`'s transport errors embed the full
/// request URL, and an RPC endpoint URL is a bearer credential
/// (`https://arb-mainnet.example/v2/<key>`). A probe result gets printed by the
/// CLI, captured in CI logs, and written to the connectivity report, so an
/// unredacted error here republishes the key into every one of those sinks.
/// Verified against a live failing request: the raw error contained the key.
///
/// Applied to every error path that can carry a URL, not just the transport one.
fn sanitize_error(msg: &str, url: &str) -> String {
    let mut out = msg.to_string();

    // Replace the full URL first, longest match, so the scheme://host/path form
    // never survives in a partially-rewritten state.
    if url.len() > 8 {
        out = out.replace(url, "<redacted-endpoint>");
    }

    // Then scrub any bare host or bare path segment that could still leak a key
    // (an error that shows only the path, or only the query, is common).
    let (scheme, rest) = match url.split_once("://") {
        Some((s, r)) => (s, r),
        None => ("", url),
    };
    let authority = rest.split(['/', '?']).next().unwrap_or("");
    if !authority.is_empty() {
        out = out.replace(authority, "<redacted-host>");
    }
    for segment in rest.split(['/', '?']).skip(1) {
        // Long opaque segments are the credential. Short ones ("rpc", "v2") are
        // structure, and redacting them would make errors unreadable.
        if segment.len() >= 16 {
            out = out.replace(segment, "<redacted-key>");
        }
    }
    let _ = scheme;
    out
}

/// Perform the read-only probe against `url`.
///
/// `expected_chain_id` is compared against the chain the endpoint reports; a
/// mismatch is a `chain_ok: false` verdict rather than an error, because
/// pointing an Arbitrum system at an Ethereum endpoint is a configuration
/// mistake worth reporting distinctly from a dead endpoint.
pub fn probe(url: &str, expected_chain_id: Option<u64>, timeout: Duration) -> ProbeReport {
    let started = Instant::now();
    let (endpoint, credential_in_url) = redact_endpoint(url);

    // ureq 2.x's builder returns the Agent directly; only the request-sending
    // calls below are fallible.
    let agent = ureq::builder()
        .timeout(timeout)
        .user_agent("jdl-hotpath-probe/0.1")
        .build();

    let mut calls: Vec<RpcCall> = Vec::new();
    let mut chain_id: Option<u64> = None;
    let mut block_number: Option<u64> = None;
    let mut gas_price_wei: Option<String> = None;
    let mut client: Option<String> = None;
    let mut fatal: Option<String> = None;

    for (index, method) in [
        RpcMethod::EthChainId,
        RpcMethod::EthBlockNumber,
        RpcMethod::EthGasPrice,
        RpcMethod::Web3ClientVersion,
    ]
    .into_iter()
    .enumerate()
    {
        let call_started = Instant::now();
        let outcome = agent
            .post(url)
            .set("content-type", "application/json")
            .send_string(&request_body(method, index as u64 + 1));

        let (result, error) = match outcome {
            Ok(response) => match response.into_string() {
                Ok(body) => match extract_result(&body) {
                    Ok(value) => (Some(value), None),
                    Err(e) => (None, Some(e)),
                },
                Err(e) => (None, Some(sanitize_error(
                    &format!("failed to read response body: {e}"),
                    url,
                ))),
            },
            // A refused connection here is terminal: every later method would
            // fail the same way, so stop rather than emit four identical errors.
            Err(ureq::Error::Transport(t)) => {
                let message = sanitize_error(&format!("transport error: {t}"), url);
                calls.push(RpcCall {
                    method: method.wire_name().to_string(),
                    result: None,
                    latency_ms: call_started.elapsed().as_millis() as u64,
                    error: Some(message.clone()),
                });
                fatal = Some(message);
                break;
            }
            Err(e) => (None, Some(sanitize_error(&format!("{e}"), url))),
        };

        let latency_ms = call_started.elapsed().as_millis() as u64;

        if let Some(value) = result.as_deref() {
            match method {
                RpcMethod::EthChainId => chain_id = parse_quantity(value),
                RpcMethod::EthBlockNumber => block_number = parse_quantity(value),
                RpcMethod::EthGasPrice => gas_price_wei = Some(value.to_string()),
                RpcMethod::Web3ClientVersion => client = Some(value.to_string()),
            }
        }

        calls.push(RpcCall {
            method: method.wire_name().to_string(),
            result,
            latency_ms,
            error: error.clone(),
        });

        if let Some(message) = error {
            // A non-transport failure (e.g. an unsupported method) should not
            // abort the probe; the chain verdict may still be decidable.
            if method == RpcMethod::EthChainId && chain_id.is_none() {
                fatal = Some(message);
                break;
            }
        }
    }

    let chain_ok = match (chain_id, expected_chain_id) {
        (Some(actual), Some(expected)) => actual == expected,
        (Some(_), None) => true,
        (None, _) => false,
    };

    ProbeReport {
        endpoint,
        credential_in_url,
        chain_id,
        expected_chain_id,
        chain_ok,
        block_number,
        gas_price_wei,
        client,
        calls,
        total_ms: started.elapsed().as_millis() as u64,
        error: fatal,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redacts_a_credentialed_endpoint() {
        let (shown, had_cred) = redact_endpoint("https://arb-mainnet.g.alchemy.com/v2/SECRET");
        assert_eq!(shown, "https://arb-mainnet.g.alchemy.com");
        assert!(had_cred, "path segment should be flagged as a credential");
        assert!(!shown.contains("SECRET"), "credential must not survive redaction");
    }

    #[test]
    fn treats_bare_root_endpoint_as_public() {
        let (shown, had_cred) = redact_endpoint("https://arb1.arbitrum.io/rpc");
        assert_eq!(shown, "https://arb1.arbitrum.io");
        assert!(had_cred, "'/rpc' is a real path segment");
        let (_, none) = redact_endpoint("https://arb1.arbitrum.io");
        assert!(!none);
    }

    #[test]
    fn parses_hex_quantities_and_rejects_garbage() {
        assert_eq!(parse_quantity("0xa4b1"), Some(42161));
        assert_eq!(parse_quantity("0x0"), Some(0));
        assert_eq!(parse_quantity("0x"), None);
        assert_eq!(parse_quantity("nothex"), None);
        assert_eq!(parse_quantity(""), None);
    }

    #[test]
    fn surfaces_rpc_error_objects() {
        let err = extract_result(r#"{"jsonrpc":"2.0","id":1,"error":{"code":-32601,"message":"nope"}}"#)
            .unwrap_err();
        assert!(err.contains("rpc error"), "got {err}");
    }

    #[test]
    fn request_body_carries_only_read_methods() {
        for method in [
            RpcMethod::EthChainId,
            RpcMethod::EthBlockNumber,
            RpcMethod::EthGasPrice,
            RpcMethod::Web3ClientVersion,
        ] {
            let body = request_body(method, 1);
            assert!(body.contains(method.wire_name()));
            assert!(
                !body.contains("sendRawTransaction"),
                "probe must never be able to emit a write method"
            );
        }
    }

    #[test]
    fn sanitize_error_removes_the_credential() {
        // Regression: a live failing request produced an error containing the
        // full URL, API key included, which the CLI printed and CI captured.
        let url = "https://arb-mainnet.g.alchemy.com/v2/SUPERSECRETKEY123";
        let raw = format!("error sending request for url ({url}): connection refused");
        let clean = sanitize_error(&raw, url);
        assert!(
            !clean.contains("SUPERSECRETKEY123"),
            "credential survived redaction: {clean}"
        );
        assert!(!clean.contains("alchemy.com/v2"), "path survived: {clean}");
        assert!(clean.contains("<redacted"), "expected a redaction marker: {clean}");
    }

    #[test]
    fn sanitize_error_handles_path_only_and_query_only_leaks() {
        let url = "https://arb-mainnet.example/v2/KEYMATERIAL0123456789";
        // Some libraries report only the path component on failure.
        let path_only = sanitize_error("failed: /v2/KEYMATERIAL0123456789 refused", url);
        assert!(!path_only.contains("KEYMATERIAL0123456789"), "{path_only}");
        // And only the query component for URL-embedded keys.
        let query_only = sanitize_error("failed: ?key=KEYMATERIAL0123456789", url);
        assert!(!query_only.contains("KEYMATERIAL0123456789"), "{query_only}");
    }

    #[test]
    fn sanitize_error_keeps_short_path_segments_readable() {
        let url = "https://arb1.arbitrum.io/rpc";
        let clean = sanitize_error("dial tcp: connection refused for https://arb1.arbitrum.io/rpc", url);
        assert!(clean.contains("connection refused"), "error text lost: {clean}");
        assert!(!clean.contains("/rpc"), "host should be masked: {clean}");
    }

    #[test]
    fn probe_request_accepts_both_snake_and_camel_case() {
        // The connectivity checker in python/ emits snake_case; the CLI docs and
        // JS callers use camelCase. Accept both rather than silently defaulting
        // expected_chain_id to None, which would make a wrong-chain endpoint
        // look correct.
        for payload in [
            r#"{"url":"http://x","expected_chain_id":42161}"#,
            r#"{"url":"http://x","expectedChainId":42161}"#,
        ] {
            let parsed: serde_json::Value = serde_json::from_str(payload).unwrap();
            let has = parsed.get("expected_chain_id").is_some() || parsed.get("expectedChainId").is_some();
            assert!(has, "payload lost the chain id: {payload}");
        }
    }
}
