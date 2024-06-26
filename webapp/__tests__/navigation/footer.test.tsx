// Copyright 2024 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the License);
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// Copyright 2024 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the License);
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

import Footer from "@/navigation/Footer"
import { INavigationItem } from "@/utils/types"
import { render, screen } from "@testing-library/react"
import { BrowserRouter } from "react-router-dom"

describe("Footer", () => {
  // beforeEach(() => {
  //   const html = document.getElementsByTagName("html")[0]
  //   if (html) {
  //     html.innerHTML = ""
  //   }
  // })

  it("contains links", () => {
    const routes: INavigationItem[] = [
      {
        name: "Foo",
        href: "/foo",
      },
      {
        name: "Bar",
        href: "/bar",
      },
      {
        name: "Baz",
        href: "/baz",
      },
    ]

    render(
      <BrowserRouter>
        <Footer routes={routes} />
      </BrowserRouter>,
    )

    const links = screen.getAllByRole("link")
    // console.log(links.map((l) => l.href))
    // console.log(links.length)

    expect(links).toHaveLength(routes.length + 1) // An extra for the hard-coded Contact Us
  })
})
