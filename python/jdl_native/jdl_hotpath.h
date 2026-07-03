/* C ABI for the jdl-hotpath Rust engine (libjdl_hotpath cdylib).
 * Each call takes a UTF-8 JSON C string and returns a newly-allocated JSON C
 * string that MUST be released with jdl_string_free. */
#ifndef JDL_HOTPATH_H
#define JDL_HOTPATH_H

char *jdl_scan(const char *input);      /* ScanRequest JSON -> ScanResult JSON   */
char *jdl_analyze(const char *input);   /* {"bytecode":"0x.."} -> AnalysisReport */
void  jdl_string_free(char *ptr);       /* free a string returned above          */

#endif /* JDL_HOTPATH_H */
