# Silencio Sans SC Regular

用于通告PDF的内嵌简体中文字体，保留Noto Sans SC的完整字符映射，不按示例文字裁剪字库。字体按随附OFL 1.1授权分发；项目代码的开源许可证不替代字体许可证。

- 上游：Google Fonts `ofl/notosanssc/NotoSansSC[wght].ttf`
- 固定提交：`a85815a42757630ce188fdad368c2dfc444d4773`
- 上游二进制SHA-256：`a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da`
- 原版权：Copyright 2014-2021 Adobe，Reserved Font Name `Source`；完整条款见OFL.txt。
- 修改：使用fonttools 4.68.0固定wght=400，保留原字符覆盖和版权，字体主名称改为Silencio Sans SC。
- 本文件SHA-256：`0b7c8027c4e9dccf6e9770bfc6ddf877bba04741a318033b1d809973d1005932`

重建步骤：从固定提交下载上游TTF及OFL.txt；用fontTools.ttLib.TTFont载入，recalcTimestamp=False，再调用instantiateVariableFont(font, {'wght':400}, inplace=True, optimize=True)。删除并重设name ID 1/2/3/4/6/16/17为`Silencio Sans SC`、`Regular`、`SilencioSansSC-Regular-1`、`Silencio Sans SC Regular`、`SilencioSansSC-Regular`、`Silencio Sans SC`、`Regular`，通过setName(text,id,3,1,0x409)保存为本目录TTF。

PDF生成时按实际使用字符内嵌字体子集，不依赖Windows字体、不联网下载字体。字库没有覆盖的字符会产生明确导出错误，仍可使用网页版。
