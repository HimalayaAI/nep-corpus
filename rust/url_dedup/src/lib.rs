use aho_corasick::AhoCorasick;
use blake3::Hasher;
use pyo3::types::PyModule;
use pyo3::{pyclass, pyfunction, pymethods, pymodule, wrap_pyfunction, Bound, PyResult};
use rayon::prelude::*;
use scraper::Html;
use std::collections::HashSet;
use unicode_normalization::UnicodeNormalization;

#[pyclass]
struct UrlSet {
    hashes: HashSet<[u8; 16]>,
}

fn hash_url(url: &str) -> [u8; 16] {
    let mut hasher = Hasher::new();
    hasher.update(url.as_bytes());
    let mut out = [0u8; 16];
    out.copy_from_slice(&hasher.finalize().as_bytes()[..16]);
    out
}

#[pymethods]
impl UrlSet {
    #[new]
    fn new() -> Self {
        Self {
            hashes: HashSet::new(),
        }
    }

    fn add(&mut self, url: &str) {
        if !url.is_empty() {
            self.hashes.insert(hash_url(url));
        }
    }

    fn add_many(&mut self, urls: Vec<String>) -> usize {
        let before = self.hashes.len();
        for url in urls {
            if !url.is_empty() {
                self.hashes.insert(hash_url(&url));
            }
        }
        self.hashes.len().saturating_sub(before)
    }

    fn contains(&self, url: &str) -> bool {
        if url.is_empty() {
            return false;
        }
        self.hashes.contains(&hash_url(url))
    }

    fn size(&self) -> usize {
        self.hashes.len()
    }

    fn __len__(&self) -> usize {
        self.hashes.len()
    }
}

/// Fast Devanagari ratio — single pass, zero allocations.
#[pyfunction]
fn devanagari_ratio(text: &str) -> f64 {
    if text.is_empty() {
        return 0.0;
    }
    let mut total: u64 = 0;
    let mut devanagari: u64 = 0;
    for c in text.chars() {
        total += 1;
        let u = c as u32;
        if (0x0900..=0x097F).contains(&u) {
            devanagari += 1;
        }
    }
    if total == 0 {
        0.0
    } else {
        devanagari as f64 / total as f64
    }
}

/// Batch devanagari ratio — process many texts in one FFI call.
#[pyfunction]
fn batch_devanagari_ratio(texts: Vec<String>) -> Vec<f64> {
    texts
        .par_iter()
        .map(|t| {
            if t.is_empty() {
                return 0.0;
            }
            let mut total: u64 = 0;
            let mut dev: u64 = 0;
            for c in t.chars() {
                total += 1;
                if (0x0900..=0x097Fu32).contains(&(c as u32)) {
                    dev += 1;
                }
            }
            if total == 0 { 0.0 } else { dev as f64 / total as f64 }
        })
        .collect()
}



#[pyfunction]
fn detect_language(text: &str) -> Option<String> {
    if text.is_empty() {
        return None;
    }
    let mut devanagari_chars = 0u64;
    let mut total_chars = 0u64;

    for c in text.chars() {
        if c.is_alphabetic() {
            total_chars += 1;
            let u = c as u32;
            if (0x0900..=0x097F).contains(&u) {
                devanagari_chars += 1;
            }
        }
    }

    if total_chars == 0 {
        return None;
    }

    let ratio = devanagari_chars as f64 / total_chars as f64;
    if ratio >= 0.15 {
        Some("ne".to_string())
    } else {
        Some("en".to_string())
    }
}



fn clean_whitespace(text: &str) -> String {
    let mut cleaned = String::with_capacity(text.len());
    for word in text.split_whitespace() {
        if !cleaned.is_empty() {
            cleaned.push(' ');
        }
        cleaned.push_str(word);
    }
    cleaned
}

/// Extract text from HTML with DOM traversal and boilerplate tag pruning.
#[pyfunction]
fn extract_text(html: &str) -> String {
    let document = Html::parse_document(html);
    let mut text = String::new();

    for element in document.root_element().descendants() {
        let mut valid = true;
        let mut curr = Some(element);

        while let Some(node) = curr {
            if let scraper::node::Node::Element(e) = node.value() {
                let name = e.name();
                if name == "script"
                    || name == "style"
                    || name == "noscript"
                    || name == "svg"
                    || name == "iframe"
                    || name == "nav"
                    || name == "header"
                    || name == "footer"
                    || name == "aside"
                    || name == "form"
                    || name == "button"
                {
                    valid = false;
                    break;
                }
            }
            curr = node.parent();
        }

        if valid {
            if let scraper::node::Node::Text(t) = element.value() {
                let s = t.trim();
                if !s.is_empty() {
                    if !text.is_empty() {
                        text.push(' ');
                    }
                    text.push_str(s);
                }
            }
        }
    }

    clean_whitespace(&text)
}

/// Basic content cleaning (whitespace normalization + min-length gate).
#[pyfunction]
fn clean_content(text: &str, min_length: Option<usize>) -> String {
    let min = min_length.unwrap_or(20);
    let cleaned = clean_whitespace(text);

    if cleaned.len() >= min {
        cleaned
    } else {
        String::new()
    }
}

/// Strip bad patterns using Aho-Corasick (O(n+m) instead of O(n*k)).
#[pyfunction]
fn strip_bad_patterns(text: &str, patterns: Vec<String>) -> String {
    if patterns.is_empty() || text.is_empty() {
        return text.to_string();
    }

    let ac = AhoCorasick::new(&patterns).expect("Failed to build Aho-Corasick");
    let result = ac.replace_all(text, &vec![" "; patterns.len()]);

    // Collapse multiple spaces
    clean_whitespace(&result)
}

/// Batch pattern stripping with parallel processing.
#[pyfunction]
fn batch_strip_bad_patterns(texts: Vec<String>, patterns: Vec<String>) -> Vec<String> {
    if patterns.is_empty() {
        return texts;
    }

    let ac = AhoCorasick::new(&patterns).expect("Failed to build Aho-Corasick");
    let replacements: Vec<&str> = vec![" "; patterns.len()];

    texts
        .par_iter()
        .map(|text| {
            if text.is_empty() {
                return String::new();
            }
            let result = ac.replace_all(text, &replacements);
            clean_whitespace(&result)
        })
        .collect()
}

/// Normalize text: NFC + zero-width space removal + whitespace collapse.
#[pyfunction]
fn normalize_text(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }
    let nfc: String = text.nfc().collect();
    let no_zws: String = nfc.replace('\u{200B}', "");
    clean_whitespace(&no_zws)
}

/// Batch NFC normalization with parallel processing.
#[pyfunction]
fn batch_normalize(texts: Vec<String>) -> Vec<String> {
    texts
        .par_iter()
        .map(|text| {
            if text.is_empty() {
                return String::new();
            }
            let nfc: String = text.nfc().collect();
            let no_zws: String = nfc.replace('\u{200B}', "");
            clean_whitespace(&no_zws)
        })
        .collect()
}

/// Generate dedup keys (hash of normalized text) with parallel processing.
#[pyfunction]
fn batch_dedup_keys(texts: Vec<String>) -> Vec<String> {
    texts
        .par_iter()
        .map(|text| {
            if text.is_empty() {
                return String::new();
            }
            let nfc: String = text.nfc().collect();
            let lower = nfc.to_lowercase();
            let stripped: String = lower
                .chars()
                .map(|c| {
                    if c.is_alphanumeric() || c.is_whitespace() || (0x0900..=0x097Fu32).contains(&(c as u32)) {
                        c
                    } else {
                        ' '
                    }
                })
                .collect();
            let normed = clean_whitespace(&stripped);
            let digest = blake3_hash_hex(normed.as_bytes());
            digest
        })
        .collect()
}

fn blake3_hash_hex(data: &[u8]) -> String {
    // We use BLAKE3 truncated to match behavior, but output as hex
    let mut hasher = Hasher::new();
    hasher.update(data);
    let hash = hasher.finalize();
    let bytes = hash.as_bytes();
    format!(
        "{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}",
        bytes[0], bytes[1], bytes[2], bytes[3],
        bytes[4], bytes[5], bytes[6], bytes[7],
        bytes[8], bytes[9], bytes[10], bytes[11],
        bytes[12], bytes[13], bytes[14], bytes[15],
    )
}

/// Complete boilerplate cleaning: nav stripping → junk filtering → dedup lines → pattern stripping.
#[pyfunction]
fn clean_document(
    text: &str,
    bad_patterns: Vec<String>,
    nav_keywords: Vec<String>,
) -> String {
    if text.is_empty() {
        return String::new();
    }

    let nav_set: HashSet<String> = nav_keywords.into_iter().map(|k| k.to_lowercase()).collect();

    let mut lines: Vec<String> = text
        .lines()
        .map(|l| l.trim().to_string())
        .filter(|l| !l.is_empty())
        .collect();

    while !lines.is_empty() {
        let ln = &lines[0];
        let ln_lower = ln.to_lowercase();
        let ln_clean = ln_lower.replace(' ', "");

        let is_nav = nav_set.contains(&ln_lower)
            || nav_set.contains(&ln_clean)
            || nav_set.iter().any(|k| {
                ln_lower.starts_with(k.as_str()) && ln_lower.len() < k.len() + 5
            });

        // Short nav lines (≤3 words, all nav keywords)
        let is_short_nav = if !is_nav && ln.split_whitespace().count() <= 3 {
            ln.split_whitespace()
                .all(|w| nav_set.contains(&w.to_lowercase()))
        } else {
            false
        };

        if is_nav || is_short_nav {
            lines.remove(0);
        } else {
            break;
        }
    }

    lines.retain(|ln| {
        let s = ln.trim();
        if s.is_empty() {
            return false;
        }

        // File extensions
        let s_lower = s.to_lowercase();
        if s_lower.ends_with(".pdf")
            || s_lower.ends_with(".docx")
            || s_lower.ends_with(".xlsx")
            || s_lower.ends_with(".png")
            || s_lower.ends_with(".jpg")
        {
            return false;
        }

        // Bare dates (YYYY-MM-DD or YYYY/MM/DD)
        if is_bare_date(s) {
            return false;
        }

        // Separator lines (only dashes, dots, equals, etc.)
        if s.chars().all(|c| " -._=#*|·•".contains(c)) {
            return false;
        }

        true
    });

    let mut seen: HashSet<String> = HashSet::new();
    lines.retain(|ln| {
        let key = ln.to_lowercase();
        if seen.contains(&key) && (key.len() < 200 || key.matches(' ').count() < 10) {
            return false;
        }
        seen.insert(key);
        true
    });

    let joined = lines.join("\n");

    if bad_patterns.is_empty() {
        return clean_whitespace_preserve_newlines(&joined);
    }

    let ac = AhoCorasick::new(&bad_patterns).expect("Aho-Corasick build");
    let replaced = ac.replace_all(&joined, &vec![" "; bad_patterns.len()]);

    clean_whitespace_preserve_newlines(&replaced)
}

/// Like clean_whitespace but preserves newlines.
fn clean_whitespace_preserve_newlines(text: &str) -> String {
    let mut result = String::with_capacity(text.len());
    for line in text.lines() {
        let cleaned = clean_whitespace(line);
        if !cleaned.is_empty() {
            if !result.is_empty() {
                result.push('\n');
            }
            result.push_str(&cleaned);
        }
    }
    result
}

fn is_bare_date(s: &str) -> bool {
    let bytes = s.as_bytes();
    if bytes.len() != 10 {
        return false;
    }
    bytes[0].is_ascii_digit()
        && bytes[1].is_ascii_digit()
        && bytes[2].is_ascii_digit()
        && bytes[3].is_ascii_digit()
        && (bytes[4] == b'-' || bytes[4] == b'/')
        && bytes[5].is_ascii_digit()
        && bytes[6].is_ascii_digit()
        && (bytes[7] == b'-' || bytes[7] == b'/')
        && bytes[8].is_ascii_digit()
        && bytes[9].is_ascii_digit()
}

/// Batch document cleaning with parallel processing and pattern reuse.
#[pyfunction]
fn batch_clean_documents(
    texts: Vec<String>,
    bad_patterns: Vec<String>,
    nav_keywords: Vec<String>,
) -> Vec<String> {
    let nav_set: HashSet<String> = nav_keywords.into_iter().map(|k| k.to_lowercase()).collect();

    let ac = if !bad_patterns.is_empty() {
        Some(AhoCorasick::new(&bad_patterns).expect("Aho-Corasick build"))
    } else {
        None
    };
    let replacements: Vec<&str> = vec![" "; bad_patterns.len()];

    texts
        .par_iter()
        .map(|text| {
            if text.is_empty() {
                return String::new();
            }

            let mut lines: Vec<String> = text
                .lines()
                .map(|l| l.trim().to_string())
                .filter(|l| !l.is_empty())
                .collect();

            while !lines.is_empty() {
                let ln_lower = lines[0].to_lowercase();
                let ln_clean = ln_lower.replace(' ', "");
                let is_nav = nav_set.contains(&ln_lower)
                    || nav_set.contains(&ln_clean)
                    || nav_set.iter().any(|k| {
                        ln_lower.starts_with(k.as_str()) && ln_lower.len() < k.len() + 5
                    });
                let is_short_nav = if !is_nav && lines[0].split_whitespace().count() <= 3 {
                    lines[0]
                        .split_whitespace()
                        .all(|w| nav_set.contains(&w.to_lowercase()))
                } else {
                    false
                };
                if is_nav || is_short_nav {
                    lines.remove(0);
                } else {
                    break;
                }
            }

            lines.retain(|ln| {
                let s = ln.trim();
                if s.is_empty() { return false; }
                let s_lower = s.to_lowercase();
                if s_lower.ends_with(".pdf") || s_lower.ends_with(".docx")
                    || s_lower.ends_with(".xlsx") || s_lower.ends_with(".png")
                    || s_lower.ends_with(".jpg") { return false; }
                if is_bare_date(s) { return false; }
                if s.chars().all(|c| " -._=#*|·•".contains(c)) { return false; }
                true
            });

            let mut seen: HashSet<String> = HashSet::new();
            lines.retain(|ln| {
                let key = ln.to_lowercase();
                if seen.contains(&key) && (key.len() < 200 || key.matches(' ').count() < 10) {
                    return false;
                }
                seen.insert(key);
                true
            });

            let joined = lines.join("\n");
            let cleaned = if let Some(ref automaton) = ac {
                automaton.replace_all(&joined, &replacements)
            } else {
                joined
            };
            clean_whitespace_preserve_newlines(&cleaned)
        })
        .collect()
}



#[pymodule]
#[pyo3(name = "rust_url_dedup")]
fn rust_url_dedup(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<UrlSet>()?;
    
    m.add_function(wrap_pyfunction!(extract_text, m)?)?;
    m.add_function(wrap_pyfunction!(detect_language, m)?)?;
    m.add_function(wrap_pyfunction!(clean_content, m)?)?;
    
    m.add_function(wrap_pyfunction!(devanagari_ratio, m)?)?;
    m.add_function(wrap_pyfunction!(batch_devanagari_ratio, m)?)?;
    m.add_function(wrap_pyfunction!(normalize_text, m)?)?;
    m.add_function(wrap_pyfunction!(batch_normalize, m)?)?;
    m.add_function(wrap_pyfunction!(strip_bad_patterns, m)?)?;
    m.add_function(wrap_pyfunction!(batch_strip_bad_patterns, m)?)?;
    m.add_function(wrap_pyfunction!(batch_dedup_keys, m)?)?;
    m.add_function(wrap_pyfunction!(clean_document, m)?)?;
    m.add_function(wrap_pyfunction!(batch_clean_documents, m)?)?;
    Ok(())
}
