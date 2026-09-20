{
  "targets": [
    {
      "target_name": "system_preview",
      "include_dirs": ["<(module_root_dir)/../../frontend/node_modules/node-addon-api"],
      "defines": ["NAPI_DISABLE_CPP_EXCEPTIONS"],
      "sources": [],
      "conditions": [
        ["OS=='mac'", {
          "sources": ["macos_qlpreview.mm"],
          "libraries": ["-framework Cocoa", "-framework Quartz"],
          "xcode_settings": {
            "CLANG_ENABLE_OBJC_ARC": "YES",
            "MACOSX_DEPLOYMENT_TARGET": "11.0",
            "OTHER_CPLUSPLUSFLAGS": ["-std=c++17", "-fobjc-arc"]
          }
        }],
        ["OS=='win'", {
          "sources": ["windows_preview.cpp"],
          "libraries": [
            "-lole32",
            "-loleaut32",
            "-lshell32",
            "-lshlwapi",
            "-luser32",
            "-lgdi32",
            "-luuid"
          ],
          "msvs_settings": {
            "VCCLCompilerTool": {
              "ExceptionHandling": 1,
              "AdditionalOptions": ["/utf-8", "/std:c++17"]
            }
          }
        }]
      ]
    }
  ]
}
