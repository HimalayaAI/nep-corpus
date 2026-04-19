use blake3::Hasher;
use pyo3::{pyclass, pyfunction, pymethods, pymodule, wrap_pyfunction, Bound, PyResult};
use pyo3::types::PyModule;
use scraper::Html;
use std::collections::HashSet;

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
                if name == "script" || name == "style" || name == "noscript" || name == "svg" 
                    || name == "iframe" || name == "nav" || name == "header" || name == "footer" 
                    || name == "aside" || name == "form" || name == "button" {
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

#[pyfunction]
fn detect_language(text: &str) -> Option<String> {
    if text.is_empty() {
        return None;
    }
    let mut devanagari_chars = 0;
    let mut total_chars = 0;
    
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

#[pymodule]
#[pyo3(name = "rust_url_dedup")]
fn rust_url_dedup(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<UrlSet>()?;
    m.add_function(wrap_pyfunction!(extract_text, m)?)?;
    m.add_function(wrap_pyfunction!(detect_language, m)?)?;
    m.add_function(wrap_pyfunction!(clean_content, m)?)?;
    Ok(())
}
