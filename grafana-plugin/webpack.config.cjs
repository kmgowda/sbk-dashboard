/*
 * Copyright (c) KMG. All Rights Reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 */

const CopyWebpackPlugin = require('copy-webpack-plugin');
const path = require('path');
const TerserPlugin = require('terser-webpack-plugin');
const webpack = require('webpack');

const licenseBanner = `Copyright (c) KMG. All Rights Reserved.
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0`;

module.exports = {
  context: path.resolve(__dirname, 'src'),
  devtool: false,
  entry: {module: './module.tsx'},
  externals: [
    'react',
    'react/jsx-runtime',
    'react-dom',
    'rxjs',
    /^@grafana\/data/,
    /^@grafana\/runtime/,
    /^@grafana\/ui/,
  ],
  mode: 'production',
  module: {
    rules: [
      {
        exclude: /node_modules/,
        test: /\.[tj]sx?$/,
        use: {loader: 'swc-loader', options: {jsc: {
          parser: {syntax: 'typescript', tsx: true},
          target: 'es2019',
          transform: {react: {runtime: 'automatic'}},
        }}},
      },
      {test: /\.css$/, use: ['style-loader', 'css-loader']},
    ],
  },
  optimization: {
    splitChunks: false,
    minimizer: [new TerserPlugin({
      extractComments: false,
      terserOptions: {format: {comments: /Copyright \(c\) KMG/}},
    })],
  },
  output: {
    clean: true,
    filename: '[name].js',
    library: {type: 'amd'},
    path: path.resolve(__dirname, '../src/sbk_dashboard/resources/grafana/plugins/kmg-sbkcomparison-app'),
    publicPath: 'public/plugins/kmg-sbkcomparison-app/',
    uniqueName: 'kmg-sbkcomparison-app',
  },
  plugins: [
    new webpack.BannerPlugin({banner: licenseBanner}),
    new webpack.optimize.LimitChunkCountPlugin({maxChunks: 1}),
    new CopyWebpackPlugin({patterns: [
      {from: 'plugin.json', to: 'plugin.json'},
      {from: 'README.md', to: 'README.md'},
      {from: '../LICENSE', to: 'LICENSE.txt'},
    ]}),
  ],
  resolve: {extensions: ['.ts', '.tsx', '.js']},
};
