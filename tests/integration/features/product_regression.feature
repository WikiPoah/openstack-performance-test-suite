Feature: Product regression coverage

  Scenario: The corporate web application remains usable
    Given a configured corporate web application
    When the consumer checks the supported web application paths
    Then the corporate web application should remain usable

  Scenario: A consumer can retrieve the application home page and its required resources
    Given a configured corporate web application
    When the consumer retrieves the application home page and its required resources
    Then the page delivery should remain functionally valid

  Scenario: Application services remain reachable across all tiers
    Given a configured application service environment
    When the consumer checks the public endpoints and backend listeners
    Then the application services should remain reachable
